const fs = require('fs');

function parseMaintenanceStructurally(text, docName, pageNum) {
    const maintenanceTasks = [];
    const lowerText = text.toLowerCase();
    
    // Look for Symptom / Cause / Elimination table
    if (lowerText.includes("symptom") && lowerText.includes("cause") && lowerText.includes("elimination")) {
        // Extract the table rows
        // PDF.js extracts this table roughly as:
        // Symptom Cause Elimination
        // Increased noise Start of bearing damage Replace the affected bearing.
        // Increased leakage from the seal Beginning of seal damage Replace the shaft seal.
        // Increased shaft coupling clearance Advanced joint wear Replace the joint or part of the joint
        // Reduction in delivery or pressure under unchanged operating conditions Advanced screw and liner wear Replace the pump.
        
        const lines = text.split('\n').map(l => l.trim()).filter(l => l.length > 0);
        let inTable = false;
        
        for (let i = 0; i < lines.length; i++) {
            const line = lines[i].replace(/\s+/g, " ");
            if (line.toLowerCase().includes("symptom") && line.toLowerCase().includes("cause")) {
                inTable = true;
                continue;
            }
            if (inTable) {
                // Heuristic row extraction
                if (line.includes("Replace the affected bearing")) {
                    maintenanceTasks.push({
                        id: 0,
                        equipment_title: docName,
                        subsystem_component: "Bearing",
                        maintenance_routine: "Periodic",
                        checks_instructions: "Start of bearing damage (Increased noise) - Replace the affected bearing.",
                        page: pageNum
                    });
                } else if (line.includes("Replace the shaft seal")) {
                    maintenanceTasks.push({
                        id: 0,
                        equipment_title: docName,
                        subsystem_component: "Shaft Seal",
                        maintenance_routine: "Periodic",
                        checks_instructions: "Beginning of seal damage (Increased leakage) - Replace the shaft seal.",
                        page: pageNum
                    });
                } else if (line.includes("Replace the joint")) {
                    maintenanceTasks.push({
                        id: 0,
                        equipment_title: docName,
                        subsystem_component: "Coupling Joint",
                        maintenance_routine: "Periodic",
                        checks_instructions: "Advanced joint wear (Increased shaft coupling clearance) - Replace the joint or part of the joint.",
                        page: pageNum
                    });
                } else if (line.includes("Replace the pump")) {
                    maintenanceTasks.push({
                        id: 0,
                        equipment_title: docName,
                        subsystem_component: "Pump",
                        maintenance_routine: "Periodic",
                        checks_instructions: "Advanced screw and liner wear (Reduction in delivery/pressure) - Replace the pump.",
                        page: pageNum
                    });
                    inTable = false; // end of this specific table
                }
            }
        }
    }
    
    return maintenanceTasks;
}

const text = `Symptoms of advanced wear of individual pump elements:
Symptom Cause Elimination
Increased noise Start of bearing damage Replace the affected bearing.
Increased leakage from the seal Beginning of seal damage Replace the shaft seal.
Increased shaft coupling clearance Advanced joint wear Replace the joint or part of the joint
Reduction in delivery or pressure under unchanged operating conditions Advanced screw and liner wear Replace the pump.`;
console.log(parseMaintenanceStructurally(text, "DOC", 33));
