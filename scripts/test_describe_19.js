const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');
const { createCanvas } = require('canvas');

async function run() {
  const filePath = '/Users/akshayryali/Downloads/12.BOP Control System And High Pressure Test Unit Master Parts Catalog.pdf';
  const data = new Uint8Array(fs.readFileSync(filePath));
  const pdf = await pdfjsLib.getDocument({data}).promise;
  const page = await pdf.getPage(19);
  
  const viewport = page.getViewport({ scale: 1.0 });
  const canvas = createCanvas(viewport.width, viewport.height);
  const ctx = canvas.getContext('2d');
  await page.render({ canvasContext: ctx, viewport: viewport }).promise;
  const base64Image = canvas.toDataURL('image/jpeg').split(',')[1];
  
  const fetchBody = {
    model: "llama3.2-vision:latest",
    prompt: "What is on this page? Give me a literal transcription of any tables.",
    stream: false,
    images: [base64Image]
  };

  const response = await fetch("http://localhost:11434/api/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fetchBody)
  });

  const respData = await response.json();
  console.log(respData.response);
}

run().catch(console.error);
