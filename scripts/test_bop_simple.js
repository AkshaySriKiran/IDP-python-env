const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');
const { createCanvas } = require('canvas');

async function run() {
  const filePath = '/Users/akshayryali/Downloads/12.BOP Control System And High Pressure Test Unit Master Parts Catalog.pdf';
  const data = new Uint8Array(fs.readFileSync(filePath));
  const pdf = await pdfjsLib.getDocument({data}).promise;
  const page = await pdf.getPage(15);
  
  const viewport = page.getViewport({ scale: 1.5 });
  const canvas = createCanvas(viewport.width, viewport.height);
  const ctx = canvas.getContext('2d');
  await page.render({ canvasContext: ctx, viewport: viewport }).promise;
  const base64Image = canvas.toDataURL('image/jpeg').split(',')[1];
  
  let systemPrompt = `You are an expert technical parser of industrial engineering manuals.
Your task is to analyze the text page content below and extract:
1. Maintenance routines, checks, and instructions.
2. Spare parts and components referenced in drawings or lists.

Group your extractions into two distinct JSON lists: "maintenance" and "spare_parts".
If a field is missing, not specified, or not available in the text, you MUST populate it with the string "NA". Do not use null, undefined, or empty values.

Rules for "spare_parts":
- Extract items that represent real spare parts, consumables, hardware, or components.
- For "part_name", extract the descriptive name of the component or part.
- For "part_number_code": The manufacturer's part number or code.
- For "quantity", extract the number of units.

Response MUST be strictly valid JSON (and only JSON, with no other text before or after).
CRITICAL: Do NOT return empty arrays if you see actual part names accompanied by alphanumeric codes. You MUST extract them.

CRITICAL INSTRUCTION: DO NOT use the values from the example output. If a field is missing or not found in the text, you MUST output "NA".

Example Output Structure:
{
  "maintenance": [],
  "spare_parts": [
    {
      "equipment_title": "NA",
      "subsystem_location": "NA",
      "item_no": "1",
      "part_name": "EXAMPLE_PART_NAME_DO_NOT_COPY",
      "part_number_code": "EXAMPLE_CODE",
      "drawing_model_no": "NA",
      "oem_standard_body": "NA",
      "part_categorization": "NA",
      "quantity": "1",
      "recommended_stock_qty": "NA",
      "warranty_period": "NA",
      "frequency_of_use": "NA"
    }
  ]
}

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

  const response = await fetch("http://localhost:11434/api/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fetchBody)
  });

  const respData = await response.json();
  try {
    const resultJson = JSON.parse(respData.response.trim());
    console.log("Extracted items:", resultJson.spare_parts.length);
    if (resultJson.spare_parts.length > 0) {
       console.log("First item:", resultJson.spare_parts[0]);
    }
  } catch (e) {
    console.error("Failed to parse JSON. Raw output was:");
    console.log(respData.response);
  }
}

run().catch(console.error);
