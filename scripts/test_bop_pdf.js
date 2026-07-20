const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');

async function run() {
  const filePath = '/Users/akshayryali/Downloads/12.BOP Control System And High Pressure Test Unit Master Parts Catalog.pdf';
  if (!fs.existsSync(filePath)) {
    console.log("File not found:", filePath);
    return;
  }
  
  const data = new Uint8Array(fs.readFileSync(filePath));
  const pdf = await pdfjsLib.getDocument({data}).promise;
  console.log("Total Pages in BOP PDF:", pdf.numPages);
  
  // Try to get text from page 2 (often a table of contents or first part page)
  const pageNum = Math.min(2, pdf.numPages);
  const page = await pdf.getPage(pageNum);
  const textContent = await page.getTextContent();
  const text = textContent.items.map(item => item.str).join(" ");
  
  console.log(`\n--- Extracted Text from Page ${pageNum} ---`);
  console.log("Text length:", text.length);
  if (text.length > 0) {
    console.log(text.substring(0, 500) + "...");
  } else {
    console.log("No text found. Confirmed it's a scanned image.");
  }
}

run().catch(console.error);
