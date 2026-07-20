const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');

async function testPipeline() {
  const appJsCode = fs.readFileSync('/Users/akshayryali/1/app.js', 'utf8');
  const data = new Uint8Array(fs.readFileSync('/Users/akshayryali/Downloads/90-90-989 Rev H Manual-pages/90-90-989 Rev H Manual-pages-2.pdf'));
  const doc = await pdfjsLib.getDocument({data}).promise;
  const page = await doc.getPage(6);
  const content = await page.getTextContent();
  const text = content.items.map(item => item.str).join(" ");
  console.log("TEXT PREVIEW PAGE 6:");
  console.log(text.substring(0, 300));
  
  let prompt = appJsCode.match(/const systemPrompt = `(.*?)`;/s)[1];
  prompt = prompt.replace('${cleanDocName}', '90-90-989 Rev H Manual-pages-2');
  
  const learnedPatternString = ""; 
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
  console.log("Extraction Results:");
  console.log(json.response);
}

testPipeline().catch(console.error);
