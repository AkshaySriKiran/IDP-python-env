const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');
const { createCanvas } = require('canvas');

async function run() {
  const filePath = '/Users/akshayryali/Downloads/12.BOP Control System And High Pressure Test Unit Master Parts Catalog.pdf';
  const data = new Uint8Array(fs.readFileSync(filePath));
  const pdf = await pdfjsLib.getDocument({data}).promise;
  const page = await pdf.getPage(19);
  
  const viewport = page.getViewport({ scale: 2.0 });
  const canvas = createCanvas(viewport.width, viewport.height);
  const ctx = canvas.getContext('2d');
  await page.render({ canvasContext: ctx, viewport: viewport }).promise;
  const base64Image = canvas.toDataURL('image/jpeg').split(',')[1];
  
  const fetchBody = {
    model: "llama3.2-vision:latest",
    prompt: "Extract the table rows. Output as JSON list with keys: 'item_no', 'part_name', 'part_no', 'quantity'.",
    stream: false,
    format: "json",
    images: [base64Image],
    options: { temperature: 0.1 }
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
