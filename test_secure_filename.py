from werkzeug.utils import secure_filename

filename = "RFB 001 National Communication and public awareness campaign Final.docx"
print(secure_filename("batch_jobid_" + filename))
