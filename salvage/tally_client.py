"""
TallyPrime XML Client
=====================

Handles all XML communication with TallyPrime HTTP server.
TallyPrime must be running with Tally HTTP Server enabled on port 9000.

Configuration in TallyPrime:
    F12 (Configure) → Advanced Configuration → Configuration →
    ODBC → Enable Tally HTTP Server → Yes → Port: 9000
"""

import httpx
import xml.etree.ElementTree as ET
from typing import List, Dict, Optional, Any
from datetime import date, datetime


def _fiscal_year_start() -> str:
    """Return first day of current Indian fiscal year (April 1) as YYYY-MM-DD"""
    today = date.today()
    year = today.year if today.month >= 4 else today.year - 1
    return f"{year}-04-01"


def _fiscal_year_end() -> str:
    """Return last day of current Indian fiscal year (March 31) as YYYY-MM-DD"""
    today = date.today()
    year = today.year if today.month < 4 else today.year + 1
    return f"{year}-03-31"


class TallyClient:
    """
    Client for communicating with TallyPrime via XML over HTTP.
    """

    def __init__(self, host: str = "localhost", port: int = 9000, timeout: float = 30.0):
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout
        self.headers = {"Content-Type": "application/xml"}

    async def ping(self) -> bool:
        """Check if TallyPrime is running and accessible"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    self.base_url,
                    data="<ENVELOPE></ENVELOPE>",
                    headers=self.headers
                )
                return response.status_code == 200
        except Exception:
            return False

    async def get_ledger(
        self,
        party_name: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get ledger transactions for a party.

        Args:
            party_name: Name of the ledger in Tally
            from_date: Start date (YYYY-MM-DD)
            to_date: End date (YYYY-MM-DD)

        Returns:
            Dict with ledger details and transactions
        """
        from_date = from_date or _fiscal_year_start()
        to_date = to_date or _fiscal_year_end()

        xml_request = f"""
        <ENVELOPE>
            <HEADER>
                <TALLYREQUEST>Export Data</TALLYREQUEST>
                <TYPE>Data</TYPE>
                <ID>Ledger Vouchers</ID>
            </HEADER>
            <BODY>
                <DESC>
                    <STATICVARIABLES>
                        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
                        <SVFROMDATE>{from_date}</SVFROMDATE>
                        <SVTODATE>{to_date}</SVTODATE>
                    </STATICVARIABLES>
                    <DYNAMICVARIABLES>
                        <SVLEDGERNAME>{party_name}</SVLEDGERNAME>
                    </DYNAMICVARIABLES>
                </DESC>
            </BODY>
        </ENVELOPE>
        """

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.base_url,
                data=xml_request,
                headers=self.headers
            )

            if response.status_code != 200:
                raise Exception(f"Tally error: {response.status_code}")

            return self._parse_ledger_response(response.text, party_name)

    async def get_all_ledgers(self) -> List[Dict[str, Any]]:
        """Get all ledger masters from Tally"""
        xml_request = """
        <ENVELOPE>
            <HEADER>
                <TALLYREQUEST>Export Data</TALLYREQUEST>
                <TYPE>Data</TYPE>
                <ID>Ledger</ID>
            </HEADER>
            <BODY>
                <DESC>
                    <STATICVARIABLES>
                        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
                    </STATICVARIABLES>
                </DESC>
            </BODY>
        </ENVELOPE>
        """

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.base_url,
                data=xml_request,
                headers=self.headers
            )

            if response.status_code != 200:
                raise Exception(f"Tally error: {response.status_code}")

            return self._parse_ledgers_list(response.text)

    async def get_all_groups(self) -> List[Dict[str, str]]:
        """Get all ledger groups from Tally"""
        xml_request = """
        <ENVELOPE>
            <HEADER>
                <TALLYREQUEST>Export Data</TALLYREQUEST>
                <TYPE>Data</TYPE>
                <ID>Group</ID>
            </HEADER>
            <BODY>
                <DESC>
                    <STATICVARIABLES>
                        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
                    </STATICVARIABLES>
                </DESC>
            </BODY>
        </ENVELOPE>
        """

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.base_url,
                data=xml_request,
                headers=self.headers
            )

            if response.status_code != 200:
                raise Exception(f"Tally error: {response.status_code}")

            return self._parse_groups_list(response.text)

    async def post_voucher(self, voucher: Dict[str, Any]) -> Dict[str, Any]:
        """
        Post a voucher to Tally.

        Args:
            voucher: Dict with voucher details
                - voucher_type: Receipt, Payment, Sales, Purchase, Journal, Contra
                - date: YYYY-MM-DD
                - party_name: Ledger name
                - amount: Decimal amount
                - narration: String
                - reference: Optional reference number

        Returns:
            Dict with status and voucher number
        """
        xml_request = self._build_voucher_xml(voucher)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.base_url,
                data=xml_request,
                headers=self.headers
            )

            if response.status_code != 200:
                raise Exception(f"Tally error: {response.status_code}")

            return {
                "status": "success",
                "message": "Voucher posted successfully",
                "voucher_number": voucher.get("voucher_number", "Auto")
            }

    async def get_trial_balance(
        self,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get trial balance from Tally"""
        from_date = from_date or _fiscal_year_start()
        to_date = to_date or _fiscal_year_end()

        xml_request = f"""
        <ENVELOPE>
            <HEADER>
                <TALLYREQUEST>Export Data</TALLYREQUEST>
                <TYPE>Data</TYPE>
                <ID>Trial Balance</ID>
            </HEADER>
            <BODY>
                <DESC>
                    <STATICVARIABLES>
                        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
                        <SVFROMDATE>{from_date}</SVFROMDATE>
                        <SVTODATE>{to_date}</SVTODATE>
                    </STATICVARIABLES>
                </DESC>
            </BODY>
        </ENVELOPE>
        """

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.base_url,
                data=xml_request,
                headers=self.headers
            )

            if response.status_code != 200:
                raise Exception(f"Tally error: {response.status_code}")

            return self._parse_trial_balance(response.text)

    async def get_outstanding(
        self,
        party_type: str = "Sundry Debtors",
        as_of_date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get outstanding receivables/payables"""
        as_of_date = as_of_date or str(date.today())

        xml_request = f"""
        <ENVELOPE>
            <HEADER>
                <TALLYREQUEST>Export Data</TALLYREQUEST>
                <TYPE>Data</TYPE>
                <ID>Outstanding Receivables</ID>
            </HEADER>
            <BODY>
                <DESC>
                    <STATICVARIABLES>
                        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
                        <SVFROMDATE>{_fiscal_year_start()}</SVFROMDATE>
                        <SVTODATE>{as_of_date}</SVTODATE>
                    </STATICVARIABLES>
                    <DYNAMICVARIABLES>
                        <SVLEDGERNAME>{party_type}</SVLEDGERNAME>
                    </DYNAMICVARIABLES>
                </DESC>
            </BODY>
        </ENVELOPE>
        """

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.base_url,
                data=xml_request,
                headers=self.headers
            )

            if response.status_code != 200:
                raise Exception(f"Tally error: {response.status_code}")

            return self._parse_outstanding(response.text)

    # ==================== XML PARSING HELPERS ====================

    def _parse_ledger_response(self, xml_string: str, party_name: str) -> Dict[str, Any]:
        """Parse Tally ledger XML response"""
        transactions = []

        try:
            root = ET.fromstring(xml_string)

            for voucher in root.findall('.//VOUCHER'):
                txn = {
                    "id": voucher.get("REMOTEID", ""),
                    "voucher_type": voucher.get("VCHTYPE", ""),
                    "voucher_number": self._get_text(voucher, "VOUCHERNUMBER"),
                    "date": self._parse_tally_date(self._get_text(voucher, "DATE")),
                    "amount": float(self._get_text(voucher, "AMOUNT", "0")),
                    "narration": self._get_text(voucher, "NARRATION", ""),
                }
                transactions.append(txn)

            # Net closing balance = total debits - total credits
            total_debit = sum(t["amount"] for t in transactions if t["amount"] > 0)
            total_credit = sum(abs(t["amount"]) for t in transactions if t["amount"] < 0)

            return {
                "party_name": party_name,
                "transactions": transactions,
                "opening_balance": 0,
                "closing_balance": total_debit - total_credit,  # Fixed: net balance
                "transaction_count": len(transactions)
            }

        except ET.ParseError as e:
            raise Exception(f"XML parse error: {e}") from e

    def _parse_ledgers_list(self, xml_string: str) -> List[Dict[str, Any]]:
        """Parse Tally ledgers list XML response"""
        ledgers = []

        try:
            root = ET.fromstring(xml_string)

            for ledger in root.findall('.//LEDGER'):
                ledgers.append({
                    "id": ledger.get("REMOTEID", ""),
                    "name": self._get_text(ledger, "NAME"),
                    "group": self._get_text(ledger, "PARENT"),
                    "gstin": self._get_text(ledger, "REGISTRATIONTYPE", ""),
                })

            return ledgers

        except ET.ParseError as e:
            raise Exception(f"XML parse error: {e}") from e

    def _parse_groups_list(self, xml_string: str) -> List[Dict[str, str]]:
        """Parse Tally groups list XML response"""
        groups = []

        try:
            root = ET.fromstring(xml_string)

            for group in root.findall('.//GROUP'):
                groups.append({
                    "name": self._get_text(group, "NAME"),
                    "parent": self._get_text(group, "PARENT"),
                })

            return groups

        except ET.ParseError as e:
            raise Exception(f"XML parse error: {e}") from e

    def _parse_trial_balance(self, xml_string: str) -> Dict[str, Any]:
        """Parse Tally trial balance XML response"""
        ledgers = []
        try:
            root = ET.fromstring(xml_string)
            for ledger in root.findall('.//LEDGER'):
                name = self._get_text(ledger, "NAME")
                amount = float(self._get_text(ledger, "CLOSINGBALANCE", "0"))
                if name:
                    ledgers.append({"name": name, "amount": amount})
        except ET.ParseError:
            pass
        return {"status": "success", "data": ledgers}

    def _parse_outstanding(self, xml_string: str) -> List[Dict[str, Any]]:
        """Parse Tally outstanding XML response"""
        items = []
        try:
            root = ET.fromstring(xml_string)
            for entry in root.findall('.//BILLALLOCATIONS.LIST'):
                name = self._get_text(entry, "NAME")
                amount = float(self._get_text(entry, "AMOUNT", "0"))
                due_date = self._get_text(entry, "BILLDATE")
                if name:
                    items.append({"bill_name": name, "amount": abs(amount), "due_date": due_date})
        except ET.ParseError:
            pass
        return items

    def _build_voucher_xml(self, voucher: Dict[str, Any]) -> str:
        """Build Tally voucher import XML"""
        voucher_type = voucher.get("voucher_type", "Receipt")
        date_str = voucher.get("date", str(date.today())).replace("-", "")
        voucher_number = voucher.get("voucher_number", "")
        party_name = voucher.get("party_name", "")
        narration = voucher.get("narration", "")
        ledger_entries = self._build_ledger_entries(voucher)

        return f"""
        <ENVELOPE>
            <HEADER>
                <TALLYREQUEST>Import Data</TALLYREQUEST>
            </HEADER>
            <BODY>
                <IMPORTDATA>
                    <REQUESTDESC>
                        <REPORTNAME>Vouchers</REPORTNAME>
                    </REQUESTDESC>
                    <REQUESTDATA>
                        <TALLYMESSAGE xmlns:UDF="TallyUDF">
                            <VOUCHER VCHTYPE="{voucher_type}" ACTION="Create">
                                <DATE>{date_str}</DATE>
                                <VOUCHERTYPENAME>{voucher_type}</VOUCHERTYPENAME>
                                <VOUCHERNUMBER>{voucher_number}</VOUCHERNUMBER>
                                <PARTYLEDGERNAME>{party_name}</PARTYLEDGERNAME>
                                {ledger_entries}
                                <NARRATION>{narration}</NARRATION>
                            </VOUCHER>
                        </TALLYMESSAGE>
                    </REQUESTDATA>
                </IMPORTDATA>
            </BODY>
        </ENVELOPE>
        """

    def _build_ledger_entries(self, voucher: Dict[str, Any]) -> str:
        """Build ledger entries for voucher"""
        entries = []
        amount = voucher.get("amount", 0)

        if voucher.get("voucher_type") in ["Receipt", "Payment"]:
            party_type = "Dr" if voucher.get("voucher_type") == "Receipt" else "Cr"
            entries.append(f"""
                <ALLLEDGERENTRIES.LIST>
                    <LEDGERNAME>{voucher.get('party_name')}</LEDGERNAME>
                    <ISDEEMEDPOSITIVE>{'Yes' if party_type == 'Dr' else 'No'}</ISDEEMEDPOSITIVE>
                    <AMOUNT>{amount if party_type == 'Dr' else -amount}</AMOUNT>
                </ALLLEDGERENTRIES.LIST>
            """)

            bank_type = "Cr" if voucher.get("voucher_type") == "Receipt" else "Dr"
            bank_name = voucher.get("bank_name", "Cash")
            entries.append(f"""
                <ALLLEDGERENTRIES.LIST>
                    <LEDGERNAME>{bank_name}</LEDGERNAME>
                    <ISDEEMEDPOSITIVE>{'Yes' if bank_type == 'Dr' else 'No'}</ISDEEMEDPOSITIVE>
                    <AMOUNT>{-amount if bank_type == 'Cr' else amount}</AMOUNT>
                </ALLLEDGERENTRIES.LIST>
            """)

        return "".join(entries)

    # ==================== UTILITY HELPERS ====================

    def _get_text(self, element: ET.Element, tag: str, default: str = "") -> str:
        """Safely get text from XML element"""
        child = element.find(tag)
        return child.text if child is not None and child.text else default

    def _parse_tally_date(self, tally_date: str) -> str:
        """Convert Tally date format (YYYYMMDD) to ISO format (YYYY-MM-DD)"""
        if not tally_date or len(tally_date) != 8:
            return str(date.today())
        try:
            return f"{tally_date[:4]}-{tally_date[4:6]}-{tally_date[6:8]}"
        except Exception:
            return str(date.today())
