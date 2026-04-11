import globals from "globals";
import pluginJs from "@eslint/js";

// See all current values with:
// npx eslint --print-config output_js/**/*.js
export default [
    {
        languageOptions: {
            ecmaVersion: 'latest',
            globals: {
                ...globals.browser,
                $: 'readonly',      // Add jQuery as a global variable
                jQuery: 'readonly', // Optionally add jQuery object if used
            },
        },
        rules: {
            'arrow-spacing': ['error', { before: true, after: true }],
            'consistent-return': 'error',
            'curly': ['error', 'all'],
            'eqeqeq': ['error', 'always'],
            'no-duplicate-imports': 'error',
            'no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
            'no-var': 'error',
            'prefer-arrow-callback': 'error',
            'prefer-const': 'error',
            'prefer-template': 'error',
        },
    },
    pluginJs.configs.recommended,
];
