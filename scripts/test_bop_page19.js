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
  
  const cleanDocName = "EXAMPLE_EQUIPMENT_DO_NOT_COPY";
  let systemPrompt = `You are an expert technical parser of industrial engineering manuals.
Your task is to analyze the text page content below and extract:
1. Maintenance routines, checks, and instructions.
2. Spare parts and components referenced in drawings or lists.

Group your extractions into two distinct JSON lists: "maintenance" and "spare_parts".
CRITICAL OPTIMIZATION: If a field is missing, not specified, or not available in the text, you MUST OMIT the key entirely from the JSON object. Do NOT output keys with "NA", null, or empty values. This is critical to save generation time.

Rules for "maintenance" tasks:
- Extract real maintenance tasks, checks, inspection routines, adjustments, or replacements.
- Clean instructions to remove page headers or random numbers. Pay special attention to tables and bulleted checklists, ensuring each item is extracted accurately.
- For "equipment_title", default to "${cleanDocName}" if the text does not mention a specific equipment.
- For "subsystem_component", you MUST identify a specific, physical sub-system or component. If a checklist implies the component, use that for all its items. If no specific component can be identified, DO NOT extract the task.
- For "maintenance_routine", extract the interval.
- For "checks_instructions", write the procedure or actions in a concise manner.

Rules for "spare_parts":
- Extract items that represent real spare parts, consumables, hardware, or components.
- For "equipment_title", default to "${cleanDocName}" if not specified.
- For "subsystem_location", identify the specific assembly or sub-system the part belongs to.
- For "part_name", extract the descriptive name of the component or part.
- For "part_categorization", use "Critical Spare", "Consumable", or "Standard Part".
- For "quantity", extract the number of units.
- For "part_number_code": The manufacturer's part number or code.
- For "drawing_model_no": The engineering drawing or model designator number.
- For "recommended_stock_qty", extract stock recommendation levels if present.
- For "frequency_of_use", extract how frequently this part is used.

Response MUST be strictly valid JSON (and only JSON, with no other text before or after).
CRITICAL EXCEPTION: Do NOT return empty arrays if you see actual part names accompanied by alphanumeric codes. You MUST extract them.

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
      "part_categorization": "Consumable",
      "quantity": "1"
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
      temperature: 0.1
    }
  };

  const response = await fetch("http://localhost:11434/api/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fetchBody)
  });

  const respData = await response.json();
  let cleanResponse = respData.response.trim();
  console.log("Raw Response:", cleanResponse);
  
}

run().catch(console.error);
