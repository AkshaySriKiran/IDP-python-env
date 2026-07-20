const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');
const appJsPath = '/Users/akshayryali/1/app.js';
const appJsCode = fs.readFileSync(appJsPath, 'utf8');

// Quick and dirty eval of shouldProcessPageWithLLM to test it
const shouldProcessCode = appJsCode.match(/function shouldProcessPageWithLLM\(pageText\).*?\n\}/s)[0];

const manifest = JSON.parse(fs.readFileSync('/Users/akshayryali/1/equipment_manifest.json', 'utf8'));

// Emulate globals
global.equipmentManifest = manifest;
global.activeEquipmentCategory = "Default";

eval(shouldProcessCode);

async function testPipeline() {
  const data = new Uint8Array(fs.readFileSync('/Users/akshayryali/Downloads/D811001583-MAN-002 03 FINAL 1-3-pages/D811001583-MAN-002 03 FINAL 1-3-pages-4.pdf'));
  const doc = await pdfjsLib.getDocument({data}).promise;
  const page = await doc.getPage(78);
  const content = await page.getTextContent();
  const text = content.items.map(item => item.str).join(" ");
  
  console.log("--- Extracted Text ---");
  console.log(text);
  
  const shouldProcess = shouldProcessPageWithLLM(text);
  console.log("\n--- Gatekeeper Filter ---");
  console.log("Passed shouldProcessPageWithLLM?", shouldProcess);
  
  if (!shouldProcess) {
    console.log("FAILED at Gatekeeper. Text rejected before LLM.");
    return;
  }
  
  console.log("\n--- Preparing Prompt ---");
  const sysPrompt = appJsCode.match(/const systemPrompt = `(.*?)`;/s)[1];
  
  // Clean up template literal
  let prompt = sysPrompt.replace('${cleanDocName}', 'D811001583-MAN-002 03 FINAL 1-3-pages-4');
  prompt = prompt.replace('${learnedPatterns.length > 0 ? \n  `CRITICAL LEARNING EXAMPLES:\\nThe user has manually corrected past extractions. You MUST strongly weigh these learned patterns when deciding how to extract and format data:\\n${JSON.stringify(learnedPatterns, null, 2)}` \n  : ""}', '');
  prompt = prompt.replace('${text}', text);
  
  console.log("Sending to Ollama...");
  
  const response = await fetch("http://localhost:11434/api/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: "llama3", // Assuming llama3, we can check UI default
      prompt: prompt,
      stream: false
    })
  });
  
  const json = await response.json();
  console.log("\n--- LLM Response ---");
  console.log(json.response);
}

testPipeline().catch(console.error);
