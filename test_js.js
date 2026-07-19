const fs = require('fs');
const acorn = require('acorn');

const html = fs.readFileSync('static/index.html', 'utf8');
const scriptMatch = html.match(/<script>([\s\S]*?)<\/script>/);

if (scriptMatch) {
    const jsCode = scriptMatch[1];
    try {
        acorn.parse(jsCode, { ecmaVersion: 2020 });
        console.log("No syntax errors found.");
    } catch (e) {
        console.error("SyntaxError:", e.message);
    }
} else {
    console.log("No script tag found.");
}
