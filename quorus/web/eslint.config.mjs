// ESLint for the relay's browser code (board-inbox.md, slice 1). No build step: these
// files are served as-is under `script-src 'self'`, so the rules that matter are the
// ones a CSP can't enforce: no HTML from data, no eval, no undefined globals.
// Run from the repo root with the website's install:
//   npx --prefix website eslint -c quorus/web/eslint.config.mjs quorus/web/*.js
import { createRequire } from "node:module";

// There is no package.json here: the plugins come from the website's install, so a
// bare `import` would not resolve them from this directory.
const require = createRequire(new URL("../../website/package.json", import.meta.url));
const js = require("@eslint/js");
const globals = require("globals");

const noHtmlSinks = ["innerHTML", "outerHTML", "insertAdjacentHTML", "write", "writeln"]
  .map((property) => ({ property, message: "data must stay text (textContent / text nodes)" }));

export default [
  { ignores: ["**/vendor/**"] },
  js.configs.recommended,
  {
    files: ["**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",  // web.js is an IIFE with "use strict"
      globals: { ...globals.browser, module: "readonly" },  // `module`: the node tests
    },
    rules: {
      "no-eval": "error",
      "no-implied-eval": "error",
      "no-new-func": "error",
      "no-restricted-properties": ["error", ...noHtmlSinks],
      "no-unused-vars": ["error", { args: "none", caughtErrors: "none" }],
    },
  },
  {
    files: ["**/quorum*.js", "**/board.js", "**/rows.js"],
    languageOptions: { sourceType: "module" },
  },
];
