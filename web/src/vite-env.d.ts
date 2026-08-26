/// <reference types="vite/client" />

// Vite's ambient types, needed by `tsc -b` as well as by the bundler. The
// catalogue test reads the app's own source through `import.meta.glob` and the
// `?raw` import suffix, and the project deliberately carries no `@types/node`
// to fall back on. Without this reference the build fails while the tests
// still pass, which is the confusing half of that failure.
