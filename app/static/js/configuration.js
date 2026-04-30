/**
 * Generic Dynamic Row Utilities for Configuration Pages
 */

const ConfigEditor = {
    /**
     * Adds a row from a <template> to a container.
     * @param {string} containerId - ID of the list container.
     * @param {string} templateId - ID of the <template> element.
     * @param {Function} customCallback - Optional callback to initialize new elements (like selects).
     */
    addRow: function(containerId, templateId, customCallback = null) {
        const list = document.getElementById(containerId);
        const templateEl = document.getElementById(templateId);
        const template = templateEl.innerHTML;
        const index = Date.now();
        const html = template.replace(/__INDEX__/g, index);

        list.insertAdjacentHTML('beforeend', html);
        const newRow = list.lastElementChild;

        list.classList.remove('hidden');
        const emptyMsg = document.getElementById(containerId + '-empty');
        if (emptyMsg) emptyMsg.classList.add('hidden');

        // Find any attribute selects in the new row and sync them
        newRow.querySelectorAll(
            'select[data-attrib-select="true"]').forEach(s => {
            this.syncAttributeUI(s, s.dataset.isRange === "true");
        });

        if (customCallback) customCallback(newRow);
    },

    /**
     * Removes a row and shows the 'empty' message if no rows remain.
     */
    removeRow: function(btn, containerId) {
        const row = btn.closest('.col-row') || btn.parentElement;
        row.remove();
        
        const list = document.getElementById(containerId);
        const remainingRows = list.querySelectorAll('.col-row:not(.header-style)');
        if (remainingRows.length === 0) {
            list.classList.add('hidden');
            const emptyMsg = document.getElementById(containerId + '-empty');
            if (emptyMsg) emptyMsg.classList.remove('hidden');
        }
    },
    
    /**
     * Attribute editing inputs: Numeric, Boolean, or Enum
     * @param select - The <select> element for the attribute ID
     * @param isRange - Set to true for Recipes (Min/Max), false for Character/Item Values
     * @param forceNumeric -- e.g. event determiner item field (not attrib)
     */
    syncAttributeUI: function(select, isRange = false, forceNumeric = false) {
        if (!select) { console.error("No select element"); return; }
        const attribId = select.value;
        const meta = ATTRIB_REGISTRY ? ATTRIB_REGISTRY[attribId] : null;

        // Find the closest container
        const row = select.closest('.factor-card') || select.closest('.col-row, .attrib-req-row, .flex-row');
        const container = row.querySelector('.value-container, .range-container, [data-slot-required]');
        if (!container) {
            console.error("Container [data-slot-required] not found in row", row);
            return;
        }

        // Hand-off from Jinja-rendered HTML data-attributes to JavaScript
        const curMin = select.dataset.currentMin || select.dataset.currentValue || "0";
        const curMax = select.dataset.currentMax || "0";

        // Determine the base name (everything before [id])
        const baseName = select.name
            .split('[id]')[0]
            .split('[attrib_id]')[0]
            .split('[item_id]')[0];

        // Field name -- 'val_required' for event factors
        const fieldName = select.dataset.fieldName || 'value';

        let html = '';

        // If no attribute is selected (e.g., placeholder) or meta missing, 
        // render a standard numeric input.
        if (forceNumeric || !meta) {
            if (isRange) {
                html = `<span class="label-like">Min:</span><input type="number" name="${baseName}[min]" value="${curMin}" step="any" style="width:70px;">
                        <span class="label-like">Max:</span><input type="number" name="${baseName}[max]" value="${curMax}" step="any" style="width:70px;">`;
            } else {
                html = `<input type="number" name="${baseName}[${fieldName}]" value="${curMin}" step="any" class="full-width" style="max-width: 8ch;" placeholder="Qty">`;
            }
        } else if (meta.is_binary) {
            if (isRange) {
                html = `<input type="hidden" name="${baseName}[min]" value="1">
                        <input type="hidden" name="${baseName}[max]" value="1">
                        <label class="checkbox-label">Required: <input type="checkbox" checked disabled></label>`;
            } else {
                const isChecked = parseFloat(curMin) > 0;
                html = `<input type="hidden" name="${baseName}[${fieldName}]" value="${isChecked ? '1' : '0'}">
                        <label class="checkbox-label"><input type="checkbox" ${isChecked ? 'checked' : ''} 
                        onchange="this.parentElement.previousElementSibling.value = this.checked ? '1' : '0'"></label>`;
            }
        } else if (meta.enums) {
            const options = (val) => meta.enums.map((label, i) => 
                `<option value="${i}" ${parseInt(val) === i ? 'selected' : ''}>${label}</option>`).join('');
            
            if (isRange) {
                html = `<span class="label-like">Min:</span><select name="${baseName}[min]">${options(curMin)}</select>
                        <span class="label-like">Max:</span><select name="${baseName}[max]">${options(curMax)}</select>`;
            } else {
                html = `<select name="${baseName}[${fieldName}]" class="full-width">${options(curMin)}</select>`;
            }
        } else {
            // Standard Number
            if (isRange) {
                html = `<span class="label-like">Min:</span><input type="number" name="${baseName}[min]" value="${curMin}" step="any" style="width:70px;">
                        <span class="label-like">Max:</span><input type="number" name="${baseName}[max]" value="${curMax}" step="any" style="width:70px;">`;
            } else {
                html = `<input type="number" name="${baseName}[${fieldName}]" value="${curMin}" step="any" class="full-width" style="max-width: 8ch;">`;
            }
        }

        container.innerHTML = html;
    }
};

// Global initializer for all config pages
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll(
        'select[data-attrib-select="true"]').forEach(s => {
        ConfigEditor.syncAttributeUI(s, s.dataset.isRange === "true");
    });
});
