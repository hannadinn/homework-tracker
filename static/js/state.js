// Shared, module-scoped app state. Exposed via getters/setters rather than
// raw exported `let` bindings, since other ES modules can't directly
// reassign an imported binding -- only the module that declares it can.

let currentClass = null;
let currentAssignment = null;

export function getCurrentClass() { return currentClass; }
export function setCurrentClass(name) { currentClass = name; }

export function getCurrentAssignment() { return currentAssignment; }
export function setCurrentAssignment(name) { currentAssignment = name; }

// Names of assignments created during this page session (not persisted --
// resets automatically on refresh, which is the desired "New" badge
// behavior). A Set's contents can be mutated by any importer without
// needing setter functions, since the Set object reference itself never
// changes -- only what's inside it does.
export const newlyCreatedAssignments = new Set();