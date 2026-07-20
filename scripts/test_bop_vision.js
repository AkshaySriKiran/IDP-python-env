const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');
const { createCanvas } = require('canvas');

async function run() {
  const filePath = '/Users/akshayryali/Downloads/12.BOP Control System And High Pressure Test Unit Master Parts Catalog.pdf';
  const data = new Uint8Array(fs.readFileSync(filePath));
  const pdf = await pdfjsLib.getDocument({data}).promise;
  
  const pageNum = 6; // Use page 6, assuming it's deeper into the manual with actual parts data
  console.log(`Extracting image from page ${pageNum}...`);
  const page = await pdf.getPage(pageNum);
  
  const viewport = page.getViewport({ scale: 1.5 });
  const canvas = createCanvas(viewport.width, viewport.height);
  const ctx = canvas.getContext('2d');
  
  await page.render({ canvasContext: ctx, viewport: viewport }).promise;
  const base64Image = canvas.toDataURL('image/jpeg').split(',')[1];

  const cleanDocName = "EXAMPLE_EQUIPMENT_DO_NOT_COPY";
  const systemPrompt = `You are an expert technical parser of industrial engineering manuals.
Your task is to analyze the text page content below and extract:
1. Maintenance routines, checks, and instructions.
2. Spare parts and components referenced in drawings or lists.

Group your extractions into two distinct JSON lists: "maintenance" and "spare_parts".
If a field is missing, not specified, or not available in the text, you MUST populate it with the string "NA". Do not use null, undefined, or empty values.

Rules for "spare_parts":
- Extract items that represent real spare parts, consumables, hardware, or components (e.g., "O-Ring", "Oil Filter Element", "Brake Disc").
- For "equipment_title", default to "${cleanDocName}" if not specified.
- For "part_categorization", use "Critical Spare", "Consumable", "Standard Part", or "NA".
- For "quantity", extract the number of units installed/used per assembly (default to "1").
- For "part_number_code": The manufacturer's part number or code.
- For "drawing_model_no": The engineering drawing or model designator number.
- For "recommended_stock_qty", extract stock recommendation levels if present (default to "NA").
- For "frequency_of_use", extract how frequently this part is used.

Response MUST be strictly valid JSON (and only JSON, with no other text before or after).
CRITICAL EXCEPTION: Do NOT return empty arrays if you see actual part names accompanied by alphanumeric codes. If specific parts exist, you MUST extract them regardless of the surrounding layout.

Text to parse:
"""
OCR VISION EXTRACTION - Use provided image to extract text.
"""`;

  const fetchBody = {
    model: "llama3.2-vision:latest",
    prompt: systemPrompt,
    stream: false,
    format: "json",
    images: [base64Image],
    options: {
      temperature: 0.0
    }
  };

  console.log(`Sending request to Ollama Vision Model for Page ${pageNum}...`);
  const response = await fetch("http://localhost:11434/api/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fetchBody)
  });

  const respData = await response.json();
  console.log("Raw Response:");
  console.log(respData.response);
}

run().catch(console.error);
