import unittest
from datetime import date
from parsers.phonepe import PhonePeParser
from parsers.generic import GenericParser
from parsers.paytm import PaytmParser
from parsers.googlepay import GooglePayParser
from parsers import get_best_parser

class TestParsers(unittest.TestCase):

    def test_phonepe_sent_extraction(self):
        text = """
        Paid Successfully
        To Rahul
        ₹5,000
        31 Aug, 08:25 PM
        Ref No: 6132 2762 5054
        phonepe
        """
        parser = PhonePeParser(text)
        t = parser.parse()
        
        self.assertEqual(t.transaction_type, "SENT")
        self.assertEqual(t.amount, 5000.0)
        self.assertEqual(t.recipient_name, "Rahul")
        self.assertEqual(t.reference_number, "613227625054")

    def test_generic_received_extraction(self):
        text = """
        Received ₹3,000
        From Arun
        Ref No: 987654321012
        31 Aug 2026, 09:10 PM
        """
        parser = GenericParser(text)
        t = parser.parse()
        
        self.assertEqual(t.transaction_type, "RECEIVED")
        self.assertEqual(t.amount, 3000.0)
        self.assertEqual(t.sender_name, "Arun")
        self.assertEqual(t.reference_number, "987654321012")

    def test_paytm_received_extraction(self):
        text = """
        10:38
        KMoneyReceived
        Amount
        R30,700
        Rupees Thirty Thousand Seven Hundred Only
        From
        LakkimsettiSaiSriVamsi
        LV
        Pay
        ViewHistory
        To
        KanagarlaPranav
        UPlID：******1141@ptyes
        UnionBankOf India-1185M
        Receivedat10:02AM,04Sep2026
        UPIRefNo:661385614715Copy
        Paytm
        """
        parser = get_best_parser(text)
        self.assertIsInstance(parser, PaytmParser)
        t = parser.parse()
        
        self.assertEqual(t.transaction_type, "RECEIVED")
        self.assertEqual(t.amount, 30700.0)
        self.assertEqual(t.sender_name, "Lakkimsetti Sai Sri Vamsi")
        self.assertEqual(t.recipient_name, "Kanagarla Pranav")
        self.assertEqual(t.reference_number, "661385614715")
        self.assertEqual(t.transaction_date, date(2026, 9, 4))
        self.assertEqual(t.transaction_time, "10:02 AM")
        self.assertEqual(t.bank_account, "1185")
        self.assertGreaterEqual(parser.get_confidence(t), 80)

    def test_googlepay_sent_extraction(self):
        text = """
        Paid to Priya
        ₹1,250.00
        25 Aug 2026 03:45 PM
        UPI transaction ID 528394829104
        To: priya@okaxis
        From: HDFC Bank
        Google Pay
        """
        parser = get_best_parser(text)
        self.assertIsInstance(parser, GooglePayParser)
        t = parser.parse()
        
        self.assertEqual(t.transaction_type, "SENT")
        self.assertEqual(t.amount, 1250.0)
        self.assertEqual(t.recipient_name, "Priya")
        self.assertEqual(t.reference_number, "528394829104")
        self.assertEqual(t.upi_id, "priya@okaxis")

    def test_sbi_receipt_extraction(self):
        text = """
        SBI
        TRANSACTION DETAILS
        Account Number: XXXXXXXX7751
        Amount: ₹ 5,000.00
        Mode of Transfer: UPI
        Transaction Date: 05/09/2026
        Narration: TRANSFER TO XXXXXXXXXX2091 UPI/DR/XXXXXXXX5052/Mohamata/SBIN/XXXXXX4778/Pay t
        """
        parser = get_best_parser(text)
        t = parser.parse()
        
        self.assertEqual(t.transaction_type, "SENT")
        self.assertEqual(t.amount, 5000.0)
        self.assertEqual(t.person_name, "Mohamata")
        self.assertEqual(t.bank_name, "State Bank of India")
        self.assertEqual(t.bank_account, "7751")
    def test_text_message_sent_yesterday(self):
        text = "Paid to balaji icic admin paid to him 5000 on yesterday"
        parser = get_best_parser(text)
        t = parser.parse()
        self.assertEqual(t.transaction_type, "SENT")
        self.assertEqual(t.amount, 5000.0)
        self.assertIn("Balaji", t.person_name)
        from datetime import timedelta
        from utils.dates import get_current_time_in_tz
        self.assertEqual(t.transaction_date, get_current_time_in_tz().date() - timedelta(days=1))

    def test_paytm_user_screenshot_extraction(self):
        text = """
        paytm
        Money Received
        ₹600
        Rupees Six Hundred Only
        Payment from PhonePe
        From: Patchigolla Lakshmi
        Vinay PV
        UPI ID: 8919991810-3@ybl
        To: Kanagarla Pranav
        UPI ID: ******1141@ptyes 8
        Union Bank Of India -
        1185
        UPI Ref No: 756482083834
        09:57 PM, 06 Sep 2026
        """
        parser = get_best_parser(text)
        self.assertIsInstance(parser, PaytmParser)
        t = parser.parse()
        
        self.assertEqual(t.transaction_type, "RECEIVED")
        self.assertEqual(t.amount, 600.0) # MUST be 600.0, NOT 1185.0
        self.assertEqual(t.person_name, "Patchigolla Lakshmi Vinay") # MUST include wrapped surname
        self.assertEqual(t.sender_name, "Patchigolla Lakshmi Vinay")
        self.assertEqual(t.recipient_name, "Kanagarla Pranav")
        self.assertEqual(t.reference_number, "756482083834")
        self.assertEqual(t.bank_name, "Union Bank of India")
        self.assertEqual(t.bank_account, "1185")
        self.assertEqual(t.transaction_time, "09:57 PM")
        self.assertEqual(t.transaction_date, date(2026, 9, 6))

if __name__ == '__main__':
    unittest.main()
