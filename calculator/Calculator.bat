   @echo off
   REM Change directory to your calculator project
   cd /d C:\Users\LENOVO-PC\Desktop\calculator

   REM Start the Flask app in a new terminal window
   start cmd /k "python app.py"

   REM Wait a few seconds for the server to start (adjust if needed)
   timeout /t 3 /nobreak >nul

   REM Open the default browser to the Flask app (default port 5000)
   start http://127.0.0.1:5000