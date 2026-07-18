import re

with open('static/index.html', 'r') as f:
    content = f.read()

# JS for tender_file
js_to_add = """
        const addTenderFileBtn = document.getElementById("addTenderFileBtn");
        const tenderFile = document.getElementById("tender_file");
        const tenderFileList = document.getElementById("tender_file_list");
        const clearTenderFileBtn = document.getElementById("clear_tender_file");

        if(addTenderFileBtn) {
            addTenderFileBtn.addEventListener("click", () => tenderFile.click());
        }
        
        if(tenderFile) {
            tenderFile.addEventListener("change", () => {
                if(tenderFile.files.length > 0) {
                    tenderFileList.innerHTML = tenderFile.files[0].name;
                    clearTenderFileBtn.style.display = "inline-block";
                } else {
                    tenderFileList.innerHTML = "";
                    clearTenderFileBtn.style.display = "none";
                }
            });
        }
        
        if(clearTenderFileBtn) {
            clearTenderFileBtn.addEventListener("click", () => {
                tenderFile.value = "";
                tenderFileList.innerHTML = "";
                clearTenderFileBtn.style.display = "none";
            });
        }

        const addBidFileBtn = document.getElementById("addBidFileBtn");
        const bidFile = document.getElementById("bid_file");
        const bidFileList = document.getElementById("bid_file_list");
        const clearBidFileBtn = document.getElementById("clear_bid_file");

        if(addBidFileBtn) {
            addBidFileBtn.addEventListener("click", () => bidFile.click());
        }
        
        if(bidFile) {
            bidFile.addEventListener("change", () => {
                if(bidFile.files.length > 0) {
                    bidFileList.innerHTML = bidFile.files[0].name;
                    clearBidFileBtn.style.display = "inline-block";
                } else {
                    bidFileList.innerHTML = "";
                    clearBidFileBtn.style.display = "none";
                }
            });
        }
        
        if(clearBidFileBtn) {
            clearBidFileBtn.addEventListener("click", () => {
                bidFile.value = "";
                bidFileList.innerHTML = "";
                clearBidFileBtn.style.display = "none";
            });
        }
"""

# Insert JS before tenderSubmitForm submission logic
content = content.replace('tenderSubmitForm.addEventListener("submit",', js_to_add + '\n        tenderSubmitForm.addEventListener("submit",')

# Clean up duplicated markOutcomeBtn code
# The code is duplicated multiple times. Let's just keep one.
while content.count("// Mark outcome dropdown logic") > 1:
    # find the second occurrence and delete it up to the next </script> or script tag
    parts = content.split("// Mark outcome dropdown logic", 1)
    before = parts[0]
    after = parts[1]
    
    parts2 = after.split("// Mark outcome dropdown logic", 1)
    if len(parts2) > 1:
        content = before + "// Mark outcome dropdown logic" + parts2[0]
        # Wait, I don't want to mess it up. Let's just use regex.
        content = re.sub(r'(\s*// Mark outcome dropdown logic.*?)(?=\s*// Mark outcome dropdown logic|\s*</script>)', '', content, count=1, flags=re.DOTALL)
    else:
        break

with open('static/index.html', 'w') as f:
    f.write(content)
print("UI patched")
