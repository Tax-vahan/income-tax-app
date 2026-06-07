from pan_verification.automation.pan_automation import PanAutomation
from pan_verification.automation.pan_bulk_automation import PanBulkAutomation
from pan_verification.services.login_service import TracesLoginService
from pan_verification.utils.logger import get_logger

logger = get_logger(__name__)


class PanVerificationService:
    def __init__(self):
        self.automation = PanAutomation()
        self.bulk_automation = PanBulkAutomation()
        self.login_service = TracesLoginService()

    async def verify_pan(self, session_id: str, pan: str, form_type: str) -> dict:
        # 1. Restore and validate session
        session_data = await self.login_service.restore_session(session_id)

        # 2. Perform PAN verification
        result = await self.automation.verify_pan(session_id, session_data, pan, form_type)

        return result

    async def bulk_upload_pan(self, session_id: str, csv_bytes: bytes, filename: str) -> dict:
        """
        Upload a CSV file of PANs to TRACES and return the token number.
        """
        logger.info(f"Starting bulk PAN upload for session: {session_id}, file: {filename}")
        session_data = await self.login_service.restore_session(session_id)

        token_number = await self.bulk_automation.upload_csv(session_id, session_data, csv_bytes, filename)

        return {
            "success": True,
            "token_number": token_number,
            "message": f"CSV uploaded successfully. Use token {token_number} to check status and download."
        }

    async def check_download_status(self, session_id: str, token_number: str) -> dict:
        """
        Check if the generated file for the token is ready to download.
        """
        logger.info(f"Checking download status for token: {token_number}")
        session_data = await self.login_service.restore_session(session_id)

        result = await self.bulk_automation.check_status(session_id, session_data, token_number)

        return {
            "token_number": token_number,
            "status": result["status"],
            "is_ready": result["is_ready"]
        }

    async def download_pan_file(self, session_id: str, token_number: str) -> tuple:
        """
        Download the generated PAN verification file.
        Returns (file_bytes, filename).
        """
        logger.info(f"Downloading file for token: {token_number}")
        session_data = await self.login_service.restore_session(session_id)

        file_bytes, filename = await self.bulk_automation.download_file(session_id, session_data, token_number)

        return file_bytes, filename
