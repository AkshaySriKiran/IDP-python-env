const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');
const { createCanvas } = require('canvas');

async function run() {
  const data = new Uint8Array(fs.readFileSync('/Users/akshayryali/Downloads/Rig#14 Electrical History ccard.pdf'));
  const pdf = await pdfjsLib.getDocument({data}).promise;
  const pageNum = 4; // Let's check page 4
  const page = await pdf.getPage(pageNum);
  
  const viewport = page.getViewport({ scale: 1.5 });
  const canvas = createCanvas(viewport.width, viewport.height);
  const ctx = canvas.getContext('2d');
  
  await page.render({ canvasContext: ctx, viewport: viewport }).promise;
  
  const base64Image = canvas.toDataURL('image/jpeg').split(',')[1];

  const systemPrompt = `Please transcribe all the handwritten and printed tabular text you see in the provided image. Format it exactly as you see it.`;

  const fetchBody = {
    model: "llama3.2-vision:latest",
    prompt: systemPrompt,
    stream: false,
    images: [base64Image],
    options: {
      temperature: 0.0
    }
  };

  console.log(`Transcribing page ${pageNum}...`);
  const response = await fetch("http://localhost:11434/api/generate", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(fetchBody)
  });

  const respData = await response.json();
  console.log(respData.response);
}

run().catch(console.error);
