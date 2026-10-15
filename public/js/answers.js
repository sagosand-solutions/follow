// Interprets a stored answer against the question as it is now.
// History is never rewritten; an answer that no longer fits the current question is simply not shown as an answer.
export function currentAnswer(question, record) {
  if (!record) return null;
  if (record.outcome === 'unknown') return { outcome: 'unknown', option: null, score: null };
  const option = question.options.find((o) => o.id === record.option_id);
  if (!option) return null;
  return { outcome: 'answered', option, score: option.score };
}
