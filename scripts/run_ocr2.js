const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');
const { createCanvas, Image } = require('canvas');
const Tesseract = require('tesseract.js');

async function ocrPDFPages() {
  const data = new Uint8Array(fs.readFileSync('/Users/akshayryali/Downloads/90-90-989 Rev H Manual-pages/90-90-989 Rev H Manual-pages-2.pdf'));
  console.warn = function() {};
  const doc = await pdfjsLib.getDocument({data}).promise;
  
  const page = await doc.getPage(7);
  const viewport = page.getViewport({ scale: 2.0, rotation: 90 }); // Rotate 90 degrees!
  
  const canvas = createCanvas(viewport.width, viewport.height);
  const ctx = canvas.getContext('2d');
  
  await page.render({
    canvasContext: ctx,
    viewport: viewport
  }).promise;
  
  const buffer = canvas.toBuffer('image/png');
  
  const result = await Tesseract.recognize(
    buffer,
    'eng',
    { logger: m => {} }
  );
  
  console.log("ROTATED PAGE 7 TEXT:\n", result.data.text.substring(0, 500));
}

ocrPDFPages().catch(console.error);
