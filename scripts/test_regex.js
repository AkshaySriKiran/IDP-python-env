const str = `Here is the extracted data in JSON format:

{
  "maintenance": [],
  "spare_parts": [
    {
      "equipment_title": "MARTIN-DECKMC"
    }
  ]
}

Note: Since the provided text does not contain actual maintenance routines or specific spare parts data, I extracted only the part names mentioned in the text and populated the other fields with default values as per the rules.`;

const jsonMatch = str.match(/\{[\s\S]*\}/);
if (jsonMatch) {
  try {
    const obj = JSON.parse(jsonMatch[0]);
    console.log("Success!");
  } catch (e) {
    console.error("Parse Error:", e.message);
  }
}
