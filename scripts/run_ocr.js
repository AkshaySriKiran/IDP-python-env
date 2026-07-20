const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');
const { createCanvas, Image } = require('canvas');
const Tesseract = require('tesseract.js');

async function ocrPDFPages() {
  console.log("Loading PDF...");
  const data = new Uint8Array(fs.readFileSync('/Users/akshayryali/Downloads/90-90-989 Rev H Manual-pages/90-90-989 Rev H Manual-pages-2.pdf'));
  
  // Set up standard font data path for pdfjs to avoid warnings (or just mute them)
  console.warn = function() {};
  
  const doc = await pdfjsLib.getDocument({data}).promise;
  
  let fullOCRText = "";
  
  // We know pages 5 to 10 contain the RSPL tables
  for (let i = 5; i <= 10; i++) {
    console.log(`Rendering page ${i}...`);
    const page = await doc.getPage(i);
    const viewport = page.getViewport({ scale: 2.0 }); // higher scale for better OCR
    
    const canvas = createCanvas(viewport.width, viewport.height);
    const ctx = canvas.getContext('2d');
    
    await page.render({
      canvasContext: ctx,
      viewport: viewport
    }).promise;
    
    console.log(`Running OCR on page ${i}...`);
    const buffer = canvas.toBuffer('image/png');
    
    const result = await Tesseract.recognize(
      buffer,
      'eng',
      { logger: m => {} }
    );
    
    console.log(`OCR complete for page ${i}. Length: ${result.data.text.length}`);
    fullOCRText += `\n--- Page ${i} ---\n` + result.data.text + "\n";
  }
  
  const outputPath = '/Users/akshayryali/Downloads/90-90-989 Rev H Manual-pages/RSPL_OCR_Text.txt';
  fs.writeFileSync(outputPath, fullOCRText);
  console.log(`\nOCR text saved to: ${outputPath}`);
}

ocrPDFPages().catch(console.error);
