from pan_verification.automation.challan_bin_automation import ChallanBinAutomation
from pan_verification.services.login_service import TracesLoginService
from pan_verification.utils.logger import get_logger

logger = get_logger(__name__)


class ChallanBinService:
    def __init__(self):
        self.automation = ChallanBinAutomation()
        self.login_service = TracesLoginService()

    async def get_form_types(self, session_id: str) -> list[dict]:
        session_data = await self.login_service.restore_session(session_id)
        return await self.automation.get_form_types(session_data)

    async def get_financial_years(self, session_id: str) -> list[dict]:
        session_data = await self.login_service.restore_session(session_id)
        return await self.automation.get_financial_years(session_data)

    async def get_quarters(self, session_id: str, financial_year: str) -> list[dict]:
        session_data = await self.login_service.restore_session(session_id)
        return await self.automation.get_quarters(session_data, financial_year)

    async def get_consumption_statuses(self, session_id: str) -> list[str]:
        session_data = await self.login_service.restore_session(session_id)
        return await self.automation.get_consumption_statuses(session_data)

    async def get_challan_statuses(self, session_id: str) -> list[dict]:
        session_data = await self.login_service.restore_session(session_id)
        return await self.automation.get_challan_statuses(session_data)

    async def get_export_formats(self, session_id: str) -> list[dict]:
        session_data = await self.login_service.restore_session(session_id)
        return await self.automation.get_export_formats(session_data)

    async def search_challan_bin(
        self,
        session_id: str,
        from_date: str,
        to_date: str,
        page: int,
        size: int,
    ) -> dict:
        """Fetches all BINs in range (Claimed + Unclaimed) — no status filtering.
        TAN comes from the logged-in session, not the caller."""
        session_data = await self.login_service.restore_session(session_id)
        tan = session_data["tan"]
        logger.info(f"Challan/BIN search for TAN {tan}: {from_date} -> {to_date}, page={page}, size={size}")
        return await self.automation.search_period_of_payment(
            session_data, tan, from_date, to_date, "", None, page, size,
        )

    async def export_download(
        self,
        session_id: str,
        from_date: str,
        to_date: str,
        format_code: str = "EXCEL",
    ) -> tuple[bytes, str]:
        """Downloads all BINs in range (Claimed + Unclaimed) — no status filtering.
        TAN comes from the logged-in session, not the caller."""
        session_data = await self.login_service.restore_session(session_id)
        tan = session_data["tan"]
        logger.info(f"Challan/BIN export ({format_code}) for TAN {tan}: {from_date} -> {to_date}")
        return await self.automation.export_download(
            session_data, format_code, tan, from_date, to_date, "", None,
        )
