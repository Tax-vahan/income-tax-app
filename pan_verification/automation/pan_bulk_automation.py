"""
Bulk PAN Verification Automation for TRACES Portal.

Flow:
1. Navigate to PAN Verification upload page on legacy TRACES domain
2. Upload the CSV file
3. Click submit → capture the token/request number
4. Poll status until file is ready
5. Download the generated file
"""
import asyncio
import os
import time
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from pan_verification.automation.browser_manager import BrowserManager
from pan_verification.utils.config import settings
from pan_verification.utils.logger import get_logger
from pan_verification.utils.errors import PortalTimeoutError, NavigationFailureError

logger = get_logger(__name__)

# TRACES legacy domain URLs
LEGACY_BASE = "https://traces61.tdscpc.gov.in"
PAN_VERIFY_UPLOAD_URL = f"{LEGACY_BASE}/app/ded/panVerification.xhtml"
REQUEST_STATUS_URL = f"{LEGACY_BASE}/app/ded/requestedDownloads.xhtml"

# Directory to save downloaded files (volume-mounted)
DOWNLOAD_DIR = "/app/downloads"


class PanBulkAutomation:

    async def upload_csv(self, session_id: str, session_data: dict, csv_bytes: bytes, filename: str) -> str:
        """
        Upload the CSV file via the API and return the token number.
        """
        context = await BrowserManager.get_context()
        try:
            logger.info(f"Starting API bulk PAN CSV upload for session: {session_id}")

            access_token = session_data.get("access_token")
            user_id = session_data.get("tan")
            if not access_token or not user_id:
                raise NavigationFailureError("Missing access token or TAN for API upload.")

            api_url = "https://traces-app.tdscpc.gov.in/validationservice/api/pan-verify/submit"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "*/*"
            }
            
            # Send the multipart form data using context.request
            response = await context.request.post(
                api_url,
                headers=headers,
                timeout=120000,
                multipart={
                    "userId": user_id,
                    "file": {
                        "name": filename,
                        "mimeType": "text/csv",
                        "buffer": csv_bytes
                    }
                }
            )
            
            if response.status != 200:
                body = await response.text()
                logger.error(f"API upload failed with status {response.status}: {body}")
                raise PortalTimeoutError(f"API upload failed: HTTP {response.status}")
                
            data = await response.json()
            
            # Extract token number
            # Response format: {"status": 200, "data": {"tokenNo": "26060594059", ...}}
            token_number = data.get("data", {}).get("tokenNo")
            if not token_number:
                raise PortalTimeoutError("Could not extract token number from API response.")
                
            logger.info(f"Token number captured from API: {token_number}")
            return token_number

        except Exception as e:
            logger.error(f"Error during API CSV upload: {str(e)}")
            raise PortalTimeoutError(f"CSV upload failed: {str(e)}")


    async def check_status(self, session_id: str, session_data: dict, token_number: str) -> dict:
        """
        Check if the file is ready using the filter API.
        """
        context = await BrowserManager.get_context()
        try:
            logger.info(f"Checking status via API for token: {token_number}")
            
            access_token = session_data.get("access_token")
            user_id = session_data.get("tan")
            if not access_token or not user_id:
                raise NavigationFailureError("Missing access token or TAN for API status check.")

            api_url = f"https://traces-app.tdscpc.gov.in/validationservice/api/pan-verify/filter?userId={user_id}&tokenNo={token_number}&page=0&size=10&fromDate=&toDate=&status="
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "*/*"
            }
            
            response = await context.request.get(api_url, headers=headers)
            
            if response.status != 200:
                body = await response.text()
                logger.error(f"API status check failed with status {response.status}: {body}")
                raise PortalTimeoutError(f"API status check failed: HTTP {response.status}")
                
            data = await response.json()
            
            # Example response: {"data": {"content": [{"tokenNo": "...", "status": ["SUBMITTED", "ACCEPTEDATCPC", "AVAILABLE"]}]}}
            content = data.get("data", {}).get("content", [])
            
            status = "Unknown"
            is_ready = False
            
            for item in content:
                if item.get("tokenNo") == token_number:
                    status_list = item.get("status", [])
                    if "AVAILABLE" in status_list:
                        status = "Available"
                        is_ready = True
                    elif "REJECTED" in status_list:
                        status = "Rejected"
                    else:
                        status = "Processing"
                    break
                    
            return {"status": status, "is_ready": is_ready}

        except Exception as e:
            logger.error(f"Error checking status via API: {str(e)}")
            raise PortalTimeoutError(f"Status check failed: {str(e)}")

    async def download_file(self, session_id: str, session_data: dict, token_number: str) -> bytes:
        """
        Download the generated file for the given token number using the validationservice API.
        Returns (raw file bytes, filename).
        """
        context = await BrowserManager.get_context()
        try:
            logger.info(f"Downloading file via API for token: {token_number}")

            access_token = session_data.get("access_token")
            if not access_token:
                raise NavigationFailureError("No access token found in session data for API download.")

            api_url = f"https://traces-app.tdscpc.gov.in/validationservice/api/pan-verify/download-by-token?tokenNo={token_number}"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "*/*"
            }

            response = await context.request.get(api_url, headers=headers)
            
            if response.status != 200:
                body = await response.text()
                logger.error(f"API download failed with status {response.status}: {body}")
                raise NavigationFailureError(f"API download failed: HTTP {response.status}")
                
            file_bytes = await response.body()
            
            # Try to get filename from content-disposition
            filename = f"PAN_Data_{token_number}.csv"
            cd = response.headers.get("content-disposition", "")
            if "filename=" in cd:
                filename = cd.split("filename=")[-1].strip('"\'')
                
            # Save for debugging/records
            os.makedirs(DOWNLOAD_DIR, exist_ok=True)
            save_path = os.path.join(DOWNLOAD_DIR, filename)
            with open(save_path, "wb") as f:
                f.write(file_bytes)
                
            logger.info(f"File downloaded successfully via API: {save_path}")
            return file_bytes, filename
            
        except NavigationFailureError:
            raise
        except Exception as e:
            logger.error(f"Error downloading file via API: {str(e)}")
            raise PortalTimeoutError(f"API Download failed: {str(e)}")


