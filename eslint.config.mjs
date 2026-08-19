import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // ESLint does not read .gitignore, so without these it walks into the
    // Python virtualenv and the vendored design-system bundle and lints
    // third-party JS. That produced 24 of 25 errors and ~1,300 warnings --
    // React-internals and torch's model_dump viewer, none of it our code --
    // which buries any real finding and makes `npm run lint` slow.
    "pipeline/venv/**",
    "pipeline/**/site-packages/**",
    "ds-bundle/**",
  ]),
]);

export default eslintConfig;
