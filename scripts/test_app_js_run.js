const fs = require('fs');
const vm = require('vm');
const code = fs.readFileSync('/Users/akshayryali/1/app.js', 'utf-8');

// Mock browser APIs
const sandbox = {
    console: console,
    document: {
        getElementById: () => ({ addEventListener: () => {}, querySelector: () => ({ style: {} }), classList: { remove: () => {}, add: () => {} }, style: {} }),
        createElement: () => ({ classList: { add: () => {} } }),
        body: { appendChild: () => {} },
        addEventListener: () => {}
    },
    window: {
        addEventListener: () => {}
    },
    localStorage: {
        getItem: () => null,
        setItem: () => {}
    },
    setTimeout: setTimeout,
    Math: Math,
    String: String,
    Object: Object
};

vm.createContext(sandbox);

try {
    vm.runInContext(code, sandbox);
    const result = sandbox.runRuleExtractorHeuristics("Test sentence.", "test.pdf", 1);
    console.log("Runtime check passed:", result);
} catch (e) {
    console.log("Runtime error:", e);
}
