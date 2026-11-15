/* =========================================================================
   DentSense frontend config
   -------------------------------------------------------------------------
   API_BASE_URL = backend ka base URL. ISE BADAL KAR frontend kisi bhi
   backend par point ho sakta hai — index.html change karne ki zaroorat nahi.

   Values:
     ''                                -> AUTO: isi page ke host par port 8000
                                          (desktop: http://127.0.0.1:8000,
                                           phone (same Wi-Fi): http://<PC-IP>:8000)
     'http://127.0.0.1:8000'           -> local backend
     'http://192.168.100.121:8000'     -> PC ka LAN IP (phone testing)
     'https://api.example.com'         -> deployed backend

   Ek aur tareeqa: URL query param se foran override —
     http://192.168.100.121:5173/?api=http://192.168.100.121:8000
   (query param hamesha config se upar priority rakhta hai)
   ========================================================================= */
window.DS_CONFIG = {
  API_BASE_URL: ''
};
