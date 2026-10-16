import test from 'node:test';
import assert from 'node:assert/strict';
import { lessonEnding } from '../public/js/views/lesson-end.js';

const course = {
  id: 'c',
  title: 'Course',
  language: 'python',
  levels: [
    {
      level: 'basics',
      title: 'Basics',
      lessons: [
        { id: 'l1', title: 'Lesson 1', builds_on: [], questions: ['q1'] },
        { id: 'l2', title: 'Lesson 2', builds_on: ['l1'], questions: ['q2'] },
        { id: 'l3', title: 'Lesson 3', builds_on: ['l1'], questions: ['q3'] },
      ],
    },
    {
      level: 'advanced',
      title: 'Advanced',
      lessons: [{ id: 'l4', title: 'Lesson 4', builds_on: ['l2'], questions: ['q4'] }],
    },
  ],
};

test('the last lesson has no next lesson and no dependents', () => {
  assert.deepEqual(lessonEnding(course, 'l4'), { next: null, buildsOn: [] });
});

test('a middle lesson points at the next lesson and lists several dependents in course order', () => {
  assert.deepEqual(lessonEnding(course, 'l1'), {
    next: { id: 'l2', title: 'Lesson 2' },
    buildsOn: [{ id: 'l2', title: 'Lesson 2' }, { id: 'l3', title: 'Lesson 3' }],
  });
});

test('a lesson without dependents still points at the next lesson', () => {
  assert.deepEqual(lessonEnding(course, 'l3'), {
    next: { id: 'l4', title: 'Lesson 4' },
    buildsOn: [],
  });
});

test('an unknown lesson is treated as an end with no dependents', () => {
  assert.deepEqual(lessonEnding(course, 'nope'), { next: null, buildsOn: [] });
});
