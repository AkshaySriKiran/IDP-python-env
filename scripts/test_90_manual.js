const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');

async function searchFile(filePath) {
  const data = new Uint8Array(fs.readFileSync(filePath));
  const doc = await pdfjsLib.getDocument({data}).promise;
  fs.appendFileSync('manual_scan_results.txt', `\n=== Scanning ${filePath} (${doc.numPages} pages) ===\n`);
  
  for (let i = 1; i <= doc.numPages; i++) {
    const page = await doc.getPage(i);
    const content = await page.getTextContent();
    const text = content.items.map(item => item.str).join(" ");
    const lowerText = text.toLowerCase().replace(/\s+/g, ' ');
    
    if (lowerText.includes("spare parts") || lowerText.includes("maintenance") || lowerText.includes("bill of materials")) {
      fs.appendFileSync('manual_scan_results.txt', `\nPage ${i} matched keywords! Preview:\n${text.substring(0, 300)}...\n`);
    }
  }
}

async function run() {
  fs.writeFileSync('manual_scan_results.txt', '');
  await searchFile('/Users/akshayryali/Downloads/90-90-989 Rev H Manual-pages/90-90-989 Rev H Manual-pages-1.pdf');
  await searchFile('/Users/akshayryali/Downloads/90-90-989 Rev H Manual-pages/90-90-989 Rev H Manual-pages-2.pdf');
  console.log("Done. Check manual_scan_results.txt");
}

run().catch(console.error);
