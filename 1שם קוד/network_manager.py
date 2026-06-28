import asyncio
import sys
import winsdk.windows.networking.connectivity as connectivity
import winsdk.windows.networking.networkoperators as operators

class NetworkMode:
    WIFI_HOTSPOT = "WIFI_HOTSPOT"
    BLUETOOTH = "BLUETOOTH"
    NONE = "NONE"

class NetworkManager:
    def __init__(self):
        self.current_mode = NetworkMode.NONE
        self.hotspot_manager = None

    async def initialize_network(self) -> tuple[str, str]:
        """
        מנסה לאתחל נקודה חמה של Wi-Fi. 
        אם נכשל, מעביר את המערכת למצב Bluetooth.
        מחזיר סוג חיבור והודעת סטטוס.
        """
        print("[Network] Attempting to initialize Wi-Fi Hotspot via WinRT API...")
        
        # וידוא שהקוד רץ על מערכת הפעלה ווינדוס
        if sys.platform != "win32":
            self.current_mode = NetworkMode.NONE
            return self.current_mode, "Error: Windows OS is required."

        try:
            # קבלת פרופיל הרשת הנוכחי
            connection_profile = connectivity.NetworkInformation.get_internet_connection_profile()
            
            # אם אין חיבור אינטרנט פעיל, ניקח את פרופיל הרשת הראשון שזמין
            if not connection_profile:
                profiles = connectivity.NetworkInformation.get_connection_profiles()
                if profiles and len(profiles) > 0:
                    connection_profile = profiles[0]
                else:
                    raise Exception("No network adapters found.")

            # יצירת מנהל נקודה חמה עבור פרופיל הרשת
            self.hotspot_manager = operators.NetworkOperatorTetheringManager.create_from_connection_profile(connection_profile)
            
            # הדלקת הנקודה החמה
            if self.hotspot_manager.tethering_operational_state == operators.TetheringOperationalState.OFF:
                print("[Network] Starting Wi-Fi Hotspot...")
                result = await self.hotspot_manager.start_tethering_async()
                
                if result.status != operators.TetheringOperationStatus.SUCCESS:
                    raise Exception(f"Tethering operation failed with status: {result.status}")
            
            self.current_mode = NetworkMode.WIFI_HOTSPOT
            
            # שליפת שם הרשת (SSID)
            try:
                credential = self.hotspot_manager.get_current_access_point_configuration()
                ssid_name = credential.ssid
            except Exception as e:
                print(f"[Network] Could not retrieve SSID name: {e}")
                ssid_name = "Unknown SSID (Check Windows Settings)"
                
            return self.current_mode, f"Success: Wi-Fi Hotspot active. SSID: {ssid_name}"

        except Exception as e:
            # במקרה של שגיאה בהפעלת ה-Wi-Fi, נופלים בצורה מסודרת למצב בלוטוס
            print(f"[Network] Wi-Fi Hotspot failed: {e}")
            print("[Network] Falling back to Bluetooth mode...")
            
            self.current_mode = NetworkMode.BLUETOOTH
            return self.current_mode, "Notice: Wi-Fi Hotspot unavailable. Switched to Bluetooth PAN Mode. Please ensure Bluetooth is enabled and devices are paired."

    async def stop_hotspot(self):
        """סגירה מסודרת של הנקודה החמה בעת סגירת התוכנה"""
        if self.hotspot_manager and self.current_mode == NetworkMode.WIFI_HOTSPOT:
            state = self.hotspot_manager.tethering_operational_state
            if state == operators.TetheringOperationalState.ON:
                print("[Network] Stopping Wi-Fi Hotspot...")
                await self.hotspot_manager.stop_tethering_async()
                self.current_mode = NetworkMode.NONE

# קוד לבדיקת המודול באופן עצמאי
if __name__ == "__main__":
    async def main():
        manager = NetworkManager()
        mode, message = await manager.initialize_network()
        print(f"\nFinal Status -> Mode: {mode} | Message: {message}")
        
        if mode == NetworkMode.WIFI_HOTSPOT:
            input("\nPress Enter to stop hotspot and exit...")
            await manager.stop_hotspot()

    asyncio.run(main())