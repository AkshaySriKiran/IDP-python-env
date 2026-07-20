const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');
const { createCanvas, Image } = require('canvas');
const Tesseract = require('tesseract.js');

async function ocrPDFPages() {
  const data = new Uint8Array(fs.readFileSync('/Users/akshayryali/Downloads/90-90-989 Rev H Manual-pages/90-90-989 Rev H Manual-pages-2.pdf'));
  console.warn = function() {}; // Mute warnings
  const doc = await pdfjsLib.getDocument({data}).promise;
  
  let fullText = "RSPL EXTRACTED TEXT FROM OCR:\n\n";
  
  // OCR only the pages that actually have tables (7 to 10)
  for (let i = 7; i <= 10; i++) {
    console.log(`Rendering page ${i} with 90 degree rotation...`);
    const page = await doc.getPage(i);
    // Scale 3.0 for better OCR accuracy, rotated 90 degrees since it's landscape
    const viewport = page.getViewport({ scale: 3.0, rotation: 90 });
    
    const canvas = createCanvas(viewport.width, viewport.height);
    const ctx = canvas.getContext('2d');
    
    await page.render({
      canvasContext: ctx,
      viewport: viewport
    }).promise;
    
    console.log(`Running Tesseract OCR on page ${i}...`);
    const buffer = canvas.toBuffer('image/png');
    
    const result = await Tesseract.recognize(
      buffer,
      'eng',
      { logger: m => {} }
    );
    
    fullText += `\n--- Page ${i} ---\n${result.data.text}\n`;
    console.log(`Done page ${i}.`);
  }
  
  const outputPath = '/Users/akshayryali/Downloads/90-90-989 Rev H Manual-pages/RSPL_OCR_Rotated.txt';
  fs.writeFileSync(outputPath, fullText);
  console.log(`\nOCR completed. File saved at: ${outputPath}`);
}

ocrPDFPages().catch(console.error);
