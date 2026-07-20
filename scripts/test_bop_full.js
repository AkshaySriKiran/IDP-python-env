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
  
  const cleanDocName = "EXAMPLE_EQUIPMENT_DO_NOT_COPY";
  let systemPrompt = `You are an expert technical parser of industrial engineering manuals.
Your task is to analyze the text page content below and extract:
1. Maintenance routines, checks, and instructions.
2. Spare parts and components referenced in drawings or lists.

Group your extractions into two distinct JSON lists: "maintenance" and "spare_parts".
If a field is missing, not specified, or not available in the text, you MUST populate it with the string "NA". Do not use null, undefined, or empty values.

Rules for "maintenance" tasks:
- Extract real maintenance tasks, checks, inspection routines, adjustments, or replacements.
- Clean instructions to remove page headers or random numbers. Pay special attention to tables and bulleted checklists, ensuring each item is extracted accurately.
- For "equipment_title", default to "${cleanDocName}" if the text does not mention a specific equipment.
- For "subsystem_component", you MUST identify a specific, physical sub-system or component (e.g., "Drum shaft assembly", "Brake Caliper", "Oil Lubrication System"). If a checklist or table implies the component in its title/header, use that component for all its items. Do NOT use generic terms like "System Component" or "NA". If no specific component can be identified or inferred, DO NOT extract the task.
- For "maintenance_routine", extract the interval (e.g. "Daily", "Every 500 Hours", "Monthly"). If no interval is specified, output "Periodic".
- For "checks_instructions", write the procedure or actions in a concise manner, keeping the exact technical meaning intact. Ensure table rows (Symptom/Cause/Elimination) are synthesized into clear instructions.

Rules for "spare_parts":
- Extract items that represent real spare parts, consumables, hardware, or components (e.g., "O-Ring", "Oil Filter Element", "Brake Disc").
- For "equipment_title", default to "${cleanDocName}" if not specified.
- For "subsystem_location", identify the specific assembly or sub-system the part belongs to.
- For "part_name", extract the descriptive name of the component or part.
- For "part_categorization", use "Critical Spare", "Consumable", "Standard Part", or "NA".
- For "quantity", extract the number of units installed/used per assembly (default to "1").
- For "part_number_code": The manufacturer's part number or code (can be alphanumeric, short or long digits, e.g. "30123290", "51300-348-f", or "1300180930").
- For "drawing_model_no": The engineering drawing or model designator number. Do NOT confuse this with the numerical part code.
- For "recommended_stock_qty", extract stock recommendation levels if present (default to "NA").
- For "frequency_of_use", extract how frequently this part is used, replaced, or needs attention (e.g. "High", "Medium", "Low", "Replace during overhaul", "Replace every 6 months", "NA").

Response MUST be strictly valid JSON (and only JSON, with no other text before or after).
CRITICAL EXCEPTION: Do NOT return empty arrays if you see actual part names accompanied by alphanumeric codes (e.g., 'EXAMPLE_PART_NAME_DO_NOT_COPY', 'a. Part Name', etc.), even if the text contains chapter numbers like '99.99'. If specific parts exist, you MUST extract them regardless of the surrounding layout.

CRITICAL INSTRUCTION: DO NOT use the values from the example output. If a field is missing or not found in the text, you MUST output "NA".

Example Output Structure:
{
  "maintenance": [
    {
      "equipment_title": "EXAMPLE_EQUIPMENT_DO_NOT_COPY",
      "subsystem_component": "Main Brake Caliper",
      "maintenance_routine": "Daily",
      "checks_instructions": "Inspect for oil leaks."
    }
  ],
  "spare_parts": [
    {
      "equipment_title": "EXAMPLE_EQUIPMENT_DO_NOT_COPY",
      "subsystem_location": "Regulator",
      "item_no": "1",
      "part_name": "EXAMPLE_PART_NAME_DO_NOT_COPY",
      "part_number_code": "EXAMPLE_CODE",
      "drawing_model_no": "NA",
      "oem_standard_body": "NA",
      "part_categorization": "Consumable",
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
  let cleanResponse = respData.response.trim();
  console.log("Raw Response Length:", cleanResponse.length);
  if (cleanResponse.length > 500) {
      console.log("Response ends with:", cleanResponse.substring(cleanResponse.length - 100));
  } else {
      console.log("Response:", cleanResponse);
  }
  
  try {
     JSON.parse(cleanResponse);
     console.log("JSON Parse Success!");
  } catch(e) {
     console.log("JSON Parse Error:", e.message);
  }
}

run().catch(console.error);
