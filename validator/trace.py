# SPDX-License-Identifier: MIT
"""Precompute deterministic source traces by running trusted snippets locally."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from . import core
from .cli import Parser
from .core import ContentError, InputLimitError, SetupError, load_json
from .runner import Limits, TrustedLocalRunner

ROOT = core.ROOT
TRACE_LIMITS = Limits(python_hash_seed="0")
PROTOCOL = "__FOLLOW_TRACE_JSON__="

# This wrapper receives source and snippet stdin as JSON data, compiles the source
# under a synthetic filename, and emits trace metadata on stderr (separate from
# the student's stdout). The wrapper itself never enters the trace collector.
TRACER_TEMPLATE = r'''import dis, inspect, io, json, re, reprlib, sys, types

data = json.loads(sys.stdin.read())
source = data["source"]
snippet_stdin = data["stdin"]
steps = []
stdout_buffer = io.StringIO()
# Hold frame objects, not id(frame): a paused frame stays distinguishable even if
# another frame is created before it resumes. Exits release their state below.
frame_states = {}
instruction_cache = {}
GENERATOR_FLAGS = inspect.CO_GENERATOR | inspect.CO_COROUTINE | inspect.CO_ASYNC_GENERATOR
YIELD_OPS = {"YIELD_VALUE"}
RETURN_OPS = {"RETURN_VALUE", "RETURN_CONST"}
RAISE_OPS = {"RAISE_VARARGS", "RERAISE"}
class Tee:
    def write(self, text):
        stdout_buffer.write(text)
        return sys.__stdout__.write(text)
    def flush(self):
        sys.__stdout__.flush()

def short(value):
    if isinstance(value, types.ModuleType) or isinstance(value, (types.BuiltinFunctionType, types.BuiltinMethodType)):
        return None
    if isinstance(value, types.FunctionType):
        return "<function %s>" % value.__name__
    if isinstance(value, type):
        return "<class %s>" % value.__name__
    rep = reprlib.Repr()
    rep.maxstring = rep.maxother = rep.maxlong = 80
    rep.maxlist = rep.maxtuple = rep.maxdict = rep.maxset = rep.maxfrozenset = 8
    text = re.sub(r" at 0x[0-9a-fA-F]+", " at 0x…", rep.repr(value))
    if text.endswith("..."):
        text = text[:-3] + "…"
    return text if len(text) <= 80 else text[:79] + "…"

def instructions_for(code):
    cached = instruction_cache.get(code)
    if cached is None:
        instructions = list(dis.get_instructions(code))
        cached = (instructions, {instruction.offset: index
                                 for index, instruction in enumerate(instructions)})
        instruction_cache[code] = cached
    return cached

def opname(frame):
    instructions, offsets = instructions_for(frame.f_code)
    index = offsets.get(frame.f_lasti)
    return instructions[index].opname if index is not None else ""

def returned_at_yield(frame):
    instructions, offsets = instructions_for(frame.f_code)
    index = offsets.get(frame.f_lasti)
    if index is None:
        return False
    if instructions[index].opname in YIELD_OPS:
        return True
    # Python 3.13 can report the post-yield RESUME offset for the trace return.
    return (instructions[index].opname == "RESUME" and index > 0 and
            instructions[index - 1].opname in YIELD_OPS)

def exception_record(arg, line):
    exc_type, exc_value, _ = arg
    try:
        message = str(exc_value)[:200]
    except BaseException:
        message = "<unavailable>"
    return {"type": exc_type.__name__, "message": message, "line": max(1, line)}

class TraceLimitReached(BaseException):
    pass

def tracer(frame, event, arg):
    if frame.f_code.co_filename != "<snippet>":
        return None
    if len(steps) >= 300:
        sys.settrace(None)
        raise TraceLimitReached()
    state = frame_states.get(frame)
    semantic_event = event
    if event == "call":
        is_suspension_frame = bool(frame.f_code.co_flags & GENERATOR_FLAGS)
        if state is not None and state.get("paused") and is_suspension_frame:
            semantic_event = "resume"
            state["paused"] = False
        else:
            semantic_event = "call"
            if state is None:
                state = {}
                frame_states[frame] = state
    elif event == "exception":
        if state is None:
            state = {}
            frame_states[frame] = state
        state["pending_exception"] = exception_record(arg, frame.f_lineno)
    elif event == "return":
        if state is None:
            state = {}
            frame_states[frame] = state
        instruction = opname(frame)
        is_suspension_frame = bool(frame.f_code.co_flags & GENERATOR_FLAGS)
        if is_suspension_frame and returned_at_yield(frame):
            semantic_event = "yield"
            state["paused"] = True
        else:
            # exception trace events are provisional: except and finally both run
            # lines after one. A real RETURN opcode confirms catch/suppression;
            # RAISE/RERAISE or an escaping exception at a CALL remains unwind.
            pending = state.get("pending_exception")
            if instruction in RAISE_OPS or (pending is not None and instruction not in RETURN_OPS):
                semantic_event = "unwind"
            else:
                semantic_event = "return"
    if event in ("line", "call", "return", "exception"):
        record = {"line": max(1, frame.f_lineno), "event": semantic_event,
                  "depth": max(0, sum(1 for active in iter_frames(frame)
                                      if active.f_code.co_filename == "<snippet>") - 1),
                  "function": frame.f_code.co_name,
                  "locals": {}, "stdout_len": len(stdout_buffer.getvalue())}
        for name, value in frame.f_locals.items():
            if not name.isidentifier():
                continue
            if name.startswith("__") and name.endswith("__"):
                continue
            try:
                rendered = short(value)
            except BaseException:
                rendered = "<unavailable>"
            if rendered is not None:
                record["locals"][name] = rendered
        if semantic_event in ("return", "yield"):
            try:
                record["value"] = short(arg) or "None"
            except BaseException:
                record["value"] = "<unavailable>"
        elif semantic_event == "exception":
            record["exception"] = state["pending_exception"]
        elif semantic_event == "unwind":
            record["exception"] = state.get("pending_exception") or {
                "type": "BaseException", "message": "exception leaves this frame", "line": max(1, frame.f_lineno)}
        steps.append(record)
    if event == "return" and semantic_event != "yield":
        frame_states.pop(frame, None)
    return tracer

def iter_frames(frame):
    while frame is not None:
        yield frame
        frame = frame.f_back

result_exception = None
truncated = False
try:
    code = compile(source, "<snippet>", "exec")
    old_stdin, old_stdout = sys.stdin, sys.stdout
    sys.stdin = io.StringIO(snippet_stdin)
    sys.stdout = Tee()
    sys.settrace(tracer)
    try:
        exec(code, {"__name__": "__main__"})
    except TraceLimitReached:
        truncated = True
    except BaseException as exc:
        frame = exc.__traceback__
        line = None
        while frame is not None:
            if frame.tb_frame.f_code.co_filename == "<snippet>":
                line = frame.tb_lineno
            frame = frame.tb_next
        result_exception = {"type": type(exc).__name__, "message": str(exc)[:200], "line": line or 1}
    finally:
        sys.settrace(None)
        sys.stdin, sys.stdout = old_stdin, old_stdout
except BaseException as exc:
    result_exception = {"type": type(exc).__name__, "message": str(exc)[:200], "line": 1}
result = {"steps": steps, "stdout": stdout_buffer.getvalue(), "exception": result_exception, "truncated": truncated}
if truncated:
    result["truncated_at_step"] = len(steps)
sys.stderr.write("\n" + "__FOLLOW_TRACE_JSON__=" + json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")
'''


def _question_paths(root, files):
    if files:
        paths = [Path(name) for name in files]
    else:
        directory = root / "public/data/questions"
        if not directory.is_dir():
            raise SetupError(f"{directory}: question directory is missing")
        paths = sorted(path for path in directory.iterdir() if path.name.endswith(".json"))
    if not paths:
        raise SetupError("No question JSON files found")
    return paths


def _trace_question(question, filename, runner):
    cases = question.get("verification", {}).get("cases", [])
    snippets = []
    for snippet in question.get("snippets", []):
        matching = [case for case in cases if case.get("snippet_id") == snippet.get("id")]
        if not matching:
            raise RuntimeError(f"{filename} snippet {snippet['id']}: no verification case to bind trace input")
        case = matching[0]
        case_id = case.get("id")
        snippet_stdin = case.get("stdin", "")
        if not isinstance(case_id, str) or not case_id:
            raise RuntimeError(f"{filename} snippet {snippet['id']}: verification case has no usable id")
        if not isinstance(snippet_stdin, str):
            raise RuntimeError(f"{filename} snippet {snippet['id']} case {case_id}: stdin is not a string")
        source = snippet["code"]
        payload = json.dumps({"source": source, "stdin": snippet_stdin}, ensure_ascii=True)
        result = runner.run(TRACER_TEMPLATE, payload, TRACE_LIMITS)
        if result.status != "completed" or result.exit_code != 0:
            raise RuntimeError(f"{filename} snippet {snippet['id']}: trace runner {result.status}: {result.stderr[:300]}")
        marker = PROTOCOL
        lines = [line for line in result.stderr.splitlines() if line.startswith(marker)]
        if len(lines) != 1:
            raise RuntimeError(f"{filename} snippet {snippet['id']}: trace result missing or ambiguous")
        try:
            observed = json.loads(lines[0][len(marker):])
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(f"{filename} snippet {snippet['id']}: invalid trace result: {exc}") from exc
        if result.stdout != observed["stdout"]:
            raise RuntimeError(f"{filename} snippet {snippet['id']}: stdout capture differs from runner output")
        if "expected" in case and result.stdout != case["expected"].get("stdout", ""):
            raise RuntimeError(f"{filename} snippet {snippet['id']}: stdout differs from its verification case")
        snippets.append({"snippet_id": snippet["id"],
                         "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                         "case_id": case_id, "stdin": snippet_stdin,
                         "stdin_sha256": hashlib.sha256(snippet_stdin.encode("utf-8")).hexdigest(),
                         **observed})
    return {"schema_version": "0.2", "question_id": question["id"], "traces": snippets}


def _validate_trace(data, schema_path):
    try:
        import jsonschema
    except ImportError as exc:
        raise SetupError("Install validator requirements before trace generation") from exc
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(data),
                        key=lambda error: tuple(str(part) for part in error.path))
    except (OSError, UnicodeError, json.JSONDecodeError, jsonschema.SchemaError) as exc:
        raise SetupError(f"{schema_path}: invalid trace schema: {exc}") from exc
    if errors:
        raise RuntimeError(f"Generated trace does not match schema: {errors[0].message}")


def _serialize(data):
    return (json.dumps(data, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def _safe_child(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise SetupError(f"{relative}: path must stay below the repository root")
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise SetupError(f"{path}: symlink paths are not supported")
    return path


def _check(root, paths):
    expected = {}
    try:
        schema_path = _safe_child(root, "schema/trace.schema.json")
        for path in paths:
            question = load_json(path)
            output = _safe_child(root, Path("public/data/traces") / f"{question['id']}.json")
            expected[output] = True
            if not output.is_file():
                print(f"{output}: missing trace")
                expected[output] = None
                continue
            trace = load_json(output)
            try:
                _validate_trace(trace, schema_path)
            except (RuntimeError, SetupError) as exc:
                print(f"{output}: invalid trace: {exc}")
                expected[output] = None
                continue
            source_by_id = {snippet["id"]: hashlib.sha256(snippet["code"].encode("utf-8")).hexdigest()
                            for snippet in question["snippets"]}
            trace_entries = trace.get("traces", [])
            trace_by_id = {entry.get("snippet_id"): entry for entry in trace_entries}
            if (trace.get("schema_version") != "0.2" or trace.get("question_id") != question["id"] or
                    len(trace_by_id) != len(trace_entries) or set(trace_by_id) != set(source_by_id)):
                print(f"{output}: trace question/snippet set is stale")
                expected[output] = None
                continue
            stale = False
            for snippet_id, source_hash in source_by_id.items():
                entry = trace_by_id[snippet_id]
                if entry.get("source_sha256") != source_hash:
                    print(f"{output}: source hash is stale for snippet {snippet_id}")
                    stale = True
                    continue
                matches = [case for case in question.get("verification", {}).get("cases", [])
                           if case.get("id") == entry.get("case_id") and case.get("snippet_id") == snippet_id]
                if len(matches) != 1:
                    print(f"{output}: case {entry.get('case_id')!r} is missing, duplicated, or belongs to another snippet")
                    stale = True
                    continue
                actual_stdin = matches[0].get("stdin", "")
                if not isinstance(actual_stdin, str):
                    print(f"{output}: case {entry.get('case_id')!r} has invalid stdin")
                    stale = True
                    continue
                stdin_hash = hashlib.sha256(actual_stdin.encode("utf-8")).hexdigest()
                if entry.get("stdin") != actual_stdin or entry.get("stdin_sha256") != stdin_hash:
                    print(f"{output}: case {entry.get('case_id')!r} stdin is stale")
                    stale = True
            if stale:
                expected[output] = None
        directory = _safe_child(root, "public/data/traces")
        if directory.exists():
            for candidate in sorted(directory.glob("*.json")):
                if candidate not in expected:
                    print(f"{candidate}: orphan trace")
                    expected[candidate] = None
        if any(value is None for value in expected.values()):
            return 1
        return 0
    except (ContentError, InputLimitError, OSError, AttributeError, KeyError, TypeError, ValueError) as exc:
        print(f"trace check failed: {exc}")
        return 1


def main(argv=None, *, runner=None):
    parser = Parser(description="Generate traces for trusted code only; not a sandbox")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true", help="Check source hashes and trace coverage; never execute code")
    parser.add_argument("--runner", choices=("trusted-local",))
    parser.add_argument("--allow-trusted-code", action="store_true",
                        help="Acknowledge trusted code only: NOT a sandbox")
    parser.add_argument("files", nargs="*")
    try:
        args = parser.parse_args(argv)
        root = args.root.resolve()
        if not root.is_dir():
            raise SetupError(f"{root}: repository root is missing")
        if args.check and args.files:
            raise SetupError("--check does not accept question paths; it always checks the full corpus")
        paths = _question_paths(root, args.files)
        if args.check:
            result = _check(root, paths)
            print(f"Trace check {'passed' if result == 0 else 'failed'}; execution never requested.")
            return result
        if args.runner != "trusted-local" or not args.allow_trusted_code:
            raise SetupError("Trace generation requires --runner trusted-local --allow-trusted-code; trusted code only, never untrusted contributions")
        runner = runner or TrustedLocalRunner(allow_trusted_code=True)
        reason = runner.unavailable_reason() if hasattr(runner, "unavailable_reason") else None
        if reason:
            raise SetupError(reason)
        print("WARNING: trusted code only. No network or host filesystem isolation; NEVER run untrusted contributions.", file=sys.stderr)
        schema_path = _safe_child(root, "schema/trace.schema.json")
        output_dir = _safe_child(root, "public/data/traces")
        output_dir.mkdir(parents=True, exist_ok=True)
        outputs = []
        for path in paths:
            question = load_json(path)
            trace = _trace_question(question, path, runner)
            _validate_trace(trace, schema_path)
            target = _safe_child(root, Path("public/data/traces") / f"{question['id']}.json")
            outputs.append((target, _serialize(trace)))
        # Do not write partial generations: stage every finished payload first.
        staged = []
        try:
            for target, payload in outputs:
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".trace-", delete=False) as handle:
                    temporary = Path(handle.name)
                    staged.append((temporary, target))
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary.chmod(0o644)
            for temporary, target in staged:
                os.replace(temporary, target)
        finally:
            for temporary, _ in staged:
                temporary.unlink(missing_ok=True)
        print(f"Generated {len(outputs)} trace file(s); trusted code only.")
        return 0
    except (SetupError, ContentError, InputLimitError, OSError, RuntimeError, KeyError, ValueError) as exc:
        print(f"trace failed: {exc}")
        return 2


if __name__ == "__main__":
    # Support `python -m validator.trace ...` by delegating to the same main as
    # `python -m validator trace ...`; without this the module is a silent no-op.
    raise SystemExit(main())
