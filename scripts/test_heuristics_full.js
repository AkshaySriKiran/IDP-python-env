const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');

function isRecommendedSparePartsPage(pageText) { return false; } // Mocked since it doesn't matter for fallback

function runRuleExtractorHeuristics(text, docName, pageNum = 1) {
  const output = { maintenance: [], spare_parts: [] };
  const lowerText = text.toLowerCase();
  
  // Fake the heuristic extraction logic for spare parts just to see if it matches exactly
  const lines = text.split(/[\r\n]+/);
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const lowerS = line.toLowerCase();
    
    // Simulate isolateComponent logic for known parts
    const knownParts = [
      "Rust Preventing Material (Exxon Rust Ban # 392)",
      "Loctite 271 (red thread lock)",
      "Shielded Motor Power Cable",
      "RTD Connection",
      "Shaft key",
      "Bearing grease",
      "O-Ring"
    ];
    
    let hasPart = false;
    let extractedPart = "";
    for (let kp of knownParts) {
      if (lowerS.includes(kp.toLowerCase())) {
        hasPart = true;
        extractedPart = kp;
        break;
      }
    }
    
    if (hasPart && (lowerS.includes("spare") || lowerS.includes("part no") || lowerS.includes("model") || lowerS.includes("type") || lowerS.includes("replace") || lowerS.includes("drawing") || lowerS.includes("material") || lowerS.includes("grease") || lowerS.includes("connection") || lowerS.includes("cable") || lowerS.includes("key") || lowerS.includes("ring") || lowerS.includes("loctite"))) {
      
      output.spare_parts.push({
        id: 0,
        equipment_title: docName ? docName.replace(/\.[^/.]+$/, "") : "NA",
        subsystem_location: "System Component Location",
        item_no: "NA",
        part_name: extractedPart,
        part_number_code: "NA",
        drawing_model_no: "NA",
        oem_standard_body: "OEM",
        part_categorization: lowerS.includes("oil") || lowerS.includes("filter") || lowerS.includes("grease") ? "Consumable" : "Critical Spare",
        quantity: "1",
        recommended_stock_qty: "1",
        warranty_period: "NA",
        frequency_of_use: "NA",
        page: pageNum
      });
    }
  }
  return output;
}

async function testAll() {
  const appJsCode = fs.readFileSync('/Users/akshayryali/1/app.js', 'utf8');
  // eval the actual heuristic function from app.js to be absolutely sure
  const runRuleFuncStr = appJsCode.substring(appJsCode.indexOf('function runRuleExtractorHeuristics'), appJsCode.indexOf('// Data Normalization helper') - 1);
  const isolateCompStr = appJsCode.substring(appJsCode.indexOf('function isolateComponent'), appJsCode.indexOf('// --- File Input Handlers ---') - 1);
  const isRecStr = appJsCode.substring(appJsCode.indexOf('function isRecommendedSparePartsPage'), appJsCode.indexOf('// Function to fetch') - 1);
  const parseStr = appJsCode.substring(appJsCode.indexOf('function parseSparePartsStructurally'), appJsCode.indexOf('function shouldProcessPageWithLLM') - 1);
  const sanitizeStr = appJsCode.substring(appJsCode.indexOf('function sanitizeVal'), appJsCode.indexOf('function normalizeExtraction') - 1);
  
  eval(sanitizeStr);
  eval(isRecStr);
  eval(parseStr);
  eval(isolateCompStr);
  eval(runRuleFuncStr.replace('function runRuleExtractorHeuristics', 'function real_runRuleExtractorHeuristics'));
  
  const data = new Uint8Array(fs.readFileSync('/Users/akshayryali/Downloads/D811001583-MAN-002 03 FINAL 1-3-pages/D811001583-MAN-002 03 FINAL 1-3-pages-4.pdf'));
  const doc = await pdfjsLib.getDocument({data}).promise;
  let allSpares = [];
  for (let i = 1; i <= doc.numPages; i++) {
    const page = await doc.getPage(i);
    const content = await page.getTextContent();
    const text = content.items.map(item => item.str).join(" ");
    const result = real_runRuleExtractorHeuristics(text, "D811001583-MAN-002 03 FINAL 1-3-pages-4.pdf", i);
    allSpares.push(...result.spare_parts);
  }
  console.log("Total spares found by heuristics:", allSpares.length);
  console.log(allSpares.map(s => `${s.part_name} - Page ${s.page}`).join("\n"));
}
testAll().catch(console.error);
