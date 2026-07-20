const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');

async function testPipeline() {
  const appJsCode = fs.readFileSync('/Users/akshayryali/1/app.js', 'utf8');
  const data = new Uint8Array(fs.readFileSync('/Users/akshayryali/Downloads/D811001583-MAN-002 03 FINAL 1-3-pages/D811001583-MAN-002 03 FINAL 1-3-pages-4.pdf'));
  const doc = await pdfjsLib.getDocument({data}).promise;
  const page = await doc.getPage(78);
  const content = await page.getTextContent();
  const text = content.items.map(item => item.str).join(" ");
  
  let prompt = appJsCode.match(/const systemPrompt = `(.*?)`;/s)[1];
  prompt = prompt.replace('${cleanDocName}', 'D811001583-MAN-002 03 FINAL 1-3-pages-4');
  
  // Inject a learned pattern that looks like a strict table extraction
  const fakeLearnedPatterns = [
    {
      "equipment_title": "D811001583-MAN-002 03 FINAL 1-3-pages-4",
      "subsystem_location": "NA",
      "item_no": "1",
      "part_name": "Rust Preventing Material (Exxon Rust Ban # 392)",
      "part_number_code": "NA",
      "drawing_model_no": "NA",
      "oem_standard_body": "OEM",
      "part_categorization": "Consumable",
      "quantity": "1",
      "recommended_stock_qty": "NA",
      "warranty_period": "NA",
      "frequency_of_use": "MEDIUM_WEAR",
      "page": 42
    }
  ];
  
  const learnedPatternString = `CRITICAL LEARNING EXAMPLES:\nThe user has manually corrected past extractions. You MUST strongly weigh these learned patterns when deciding how to extract and format data:\n${JSON.stringify(fakeLearnedPatterns, null, 2)}`;
  
  prompt = prompt.replace('${learnedPatterns.length > 0 ? \n  `CRITICAL LEARNING EXAMPLES:\\nThe user has manually corrected past extractions. You MUST strongly weigh these learned patterns when deciding how to extract and format data:\\n${JSON.stringify(learnedPatterns, null, 2)}` \n  : ""}', learnedPatternString);
  
  const finalPrompt = prompt.replace('${text}', text);
  
  const response = await fetch("http://localhost:11434/api/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: "llama3",
      prompt: finalPrompt,
      stream: false,
      format: "json",
      options: { temperature: 0.0 }
    })
  });
  
  const json = await response.json();
  console.log(json.response);
}

testPipeline().catch(console.error);
