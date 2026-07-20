const fs = require('fs');
const vm = require('vm');
const code = fs.readFileSync('/Users/akshayryali/1/app.js', 'utf-8');

try {
    const script = new vm.Script(code);
    console.log("Syntax is OK");
} catch(e) {
    console.log("Syntax Error:", e);
}
