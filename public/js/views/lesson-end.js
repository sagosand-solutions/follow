// View data for the end of a finished lesson.
// Pure: it only reads the parsed course and returns plain values.

import { nextLesson, buildsOnThis } from '../course-nav.js';

// The next lesson in course order (or null at the end of the course) and every
// lesson that builds on this one, in course order.
export function lessonEnding(course, lessonId) {
  return {
    next: nextLesson(course, lessonId),
    buildsOn: buildsOnThis(course, lessonId),
  };
}
