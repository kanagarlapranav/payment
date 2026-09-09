import unittest
from datetime import date
from parsers.phonepe import PhonePeParser
from parsers.generic import GenericParser
from parsers.paytm import PaytmParser
from parsers.googlepay import GooglePayParser
from parsers.bhim import BhimParser
from parsers.extended_upi import (
    CredParser, SuperMoneyParser, NaviParser, YonoSbiParser, UnionEaseParser
)
from parsers import get_best_parser
from utils.currency import parse_amount, extract_amounts_from_line

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

    def test_paytm_sent_3500_exact_user_screenshot(self):
        text = """
        Paytm
        Kanagarla Sai Akhil
        9346896729@ybl on PhonePe
        ₹3,500
        Three Thousand Five Hundred Rupees
        Paid Successfully
        From
        Kanagarla Pranav
        Union Bank of India - 1185
        9 Sep, 10:58 AM | Ref No: 6252 4624 3251
        """
        parser = get_best_parser(text)
        self.assertIsInstance(parser, PaytmParser)
        t = parser.parse()
        
        self.assertEqual(t.transaction_type, "SENT")
        self.assertEqual(t.amount, 3500.0)
        self.assertEqual(t.person_name, "Kanagarla Sai Akhil")
        self.assertEqual(t.recipient_name, "Kanagarla Sai Akhil")
        self.assertEqual(t.sender_name, "Kanagarla Pranav")
        self.assertEqual(t.reference_number, "625246243251")
        self.assertEqual(t.bank_name, "Union Bank of India")
        self.assertEqual(t.bank_account, "1185")
        self.assertEqual(t.transaction_date, date(2026, 9, 9))
        self.assertEqual(t.transaction_time, "10:58 AM")

    def test_paytm_sent_dot_separator_and_spaces(self):
        # When OCR returns dot e.g. 3.500 or space e.g. ₹3, 500
        variations = [
            """
            Paytm
            Kanagarla Sai Akhil
            9346896729@ybl on PhonePe
            3.500
            Paid Successfully
            From
            Kanagarla Pranav
            Union Bank of India - 1185
            9 Sep, 10:58 AM | Ref No: 6252 4624 3251
            """,
            """
            Paytm
            Kanagarla Sai Akhil
            9346896729@ybl on PhonePe
            ₹3, 500
            Paid Successfully
            From
            Kanagarla Pranav
            Union Bank of India - 1185
            9 Sep, 10:58 AM | Ref No: 6252 4624 3251
            """,
            """
            Paytm
            Kanagarla Sai Akhil
            9346896729@ybl on PhonePe
            ₹3 500
            Paid Successfully
            From
            Kanagarla Pranav
            Union Bank of India - 1185
            9 Sep, 10:58 AM | Ref No: 6252 4624 3251
            """
        ]
        for var in variations:
            parser = get_best_parser(var)
            t = parser.parse()
            self.assertEqual(t.transaction_type, "SENT")
            self.assertEqual(t.amount, 3500.0)
            self.assertEqual(t.person_name, "Kanagarla Sai Akhil")
            self.assertEqual(t.recipient_name, "Kanagarla Sai Akhil")
            self.assertEqual(t.reference_number, "625246243251")

    def test_phonepe_received_4900_screenshot(self):
        text = """
        Transaction Successful
        10:44 AM on 09 Sep 2026

        Received from
        XXXXXXXX3377
        ₹4,900

        Transfer Details
        PhonePe Transaction ID
        T2609091044496419927143

        Credited to
        paytm • XXXXXX1141@ptyes
        ₹4,900
        UTR: 020250271371
        """
        parser = get_best_parser(text)
        self.assertIsInstance(parser, PhonePeParser)
        t = parser.parse()
        
        self.assertEqual(t.transaction_type, "RECEIVED")
        self.assertEqual(t.amount, 4900.0)
        self.assertEqual(t.reference_number, "020250271371")
        self.assertEqual(t.transaction_date, date(2026, 9, 9))
        self.assertEqual(t.transaction_time, "10:44 AM")

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
        self.assertEqual(t.amount, 600.0)
        self.assertEqual(t.person_name, "Patchigolla Lakshmi Vinay")
        self.assertEqual(t.sender_name, "Patchigolla Lakshmi Vinay")
        self.assertEqual(t.recipient_name, "Kanagarla Pranav")
        self.assertEqual(t.reference_number, "756482083834")
        self.assertEqual(t.bank_name, "Union Bank of India")
        self.assertEqual(t.bank_account, "1185")
        self.assertEqual(t.transaction_time, "09:57 PM")
        self.assertEqual(t.transaction_date, date(2026, 9, 6))

    def test_bhim_user_screenshot_extraction(self):
        text = """
        1:540
        todayat1:45PM
        ePaid
        ?4,000.00
        Paidin1.38Seconds
        Banking Name
        KANAGARLASAIAKHIL
        Transaction ID
        Date& Time
        134446412863
        8th Sep 26, 01:44
        pm
        To UPI ID
        From UPI ID
        .****6729@ybl
        *1141@upi
        Debited account
        Remarks
        NOREMARKS
        State Bank Of India
        X7751
        Payment instrument
        Payment mode
        Bankaccount
        Send Money
        Process details
        Payment initiated by Kanagarla Pranav
        Paymenttransferred from Kanagarla Pranav's
        account
        Payment received by KANAGARLA SAI
        AKHIL
        Hide details ↑
        Share
        PaymentdoneviaBHIMPaymentsApp
        Get
        upto300cashbackeverymonthwithBHIMApp.
        Downloadnow:https://bhim.onelink.me/CoHB
        /DownloadNow
        """
        parser = get_best_parser(text)
        self.assertIsInstance(parser, BhimParser)
        t = parser.parse()
        
        self.assertEqual(t.transaction_type, "SENT")
        self.assertEqual(t.amount, 4000.0)
        self.assertEqual(t.recipient_name, "Kanagarla Sai Akhil")
        self.assertEqual(t.sender_name, "Kanagarla Pranav")
        self.assertEqual(t.reference_number, "134446412863")
        self.assertEqual(t.bank_name, "State Bank Of India")
        self.assertEqual(t.bank_account, "X7751")
        self.assertEqual(t.payment_app, "BHIM")
        self.assertEqual(t.transaction_time, "01:44 PM")
        self.assertEqual(t.transaction_date, date(2026, 9, 8))

    def test_cred_upi_parser(self):
        text = """
        CRED UPI
        Paid to Ramesh
        ₹1,500.00
        CRED Protected
        UTR: 987654321098
        2026-09-08 02:30 PM
        """
        parser = get_best_parser(text)
        self.assertIsInstance(parser, CredParser)
        t = parser.parse()
        self.assertEqual(t.transaction_type, "SENT")
        self.assertEqual(t.amount, 1500.0)
        self.assertEqual(t.payment_app, "CRED")
        self.assertEqual(t.reference_number, "987654321098")

    def test_supermoney_parser(self):
        text = """
        Super.money UPI
        Paid successfully to Swiggy
        ₹450
        Ref: 123456789012
        """
        parser = get_best_parser(text)
        self.assertIsInstance(parser, SuperMoneyParser)
        t = parser.parse()
        self.assertEqual(t.transaction_type, "SENT")
        self.assertEqual(t.amount, 450.0)
        self.assertEqual(t.payment_app, "Super.money")

    def test_navipay_parser(self):
        text = """
        Navi UPI
        Payment of ₹800 to Electricity Board Successful
        Ref: 556677889900
        """
        parser = get_best_parser(text)
        self.assertIsInstance(parser, NaviParser)
        t = parser.parse()
        self.assertEqual(t.transaction_type, "SENT")
        self.assertEqual(t.amount, 800.0)
        self.assertEqual(t.payment_app, "Navi")

    def test_yono_sbi_parser(self):
        text = """
        YONO SBI
        Transferred ₹2,500 to Mahesh
        Ref No: 112233445566
        """
        parser = get_best_parser(text)
        self.assertIsInstance(parser, YonoSbiParser)
        t = parser.parse()
        self.assertEqual(t.transaction_type, "SENT")
        self.assertEqual(t.amount, 2500.0)
        self.assertEqual(t.payment_app, "YONO SBI")
        self.assertEqual(t.bank_name, "State Bank of India")

    def test_union_ease_parser(self):
        text = """
        Union EASE
        Payment of ₹3,200 Received from Suresh
        Ref: 998877665544
        """
        parser = get_best_parser(text)
        self.assertIsInstance(parser, UnionEaseParser)
        t = parser.parse()
        self.assertEqual(t.transaction_type, "RECEIVED")
        self.assertEqual(t.amount, 3200.0)
        self.assertEqual(t.payment_app, "Union EASE")
        self.assertEqual(t.bank_name, "Union Bank of India")

    def test_currency_ocr_artifacts(self):
        test_amounts = [
            ('3,500', 3500.0),
            ('3.500', 3500.0),
            ('₹3,500', 3500.0),
            ('₹3.500', 3500.0),
            ('₹3, 500', 3500.0),
            ('₹3 500', 3500.0),
            ('3,500.00', 3500.0),
            ('3.500,00', 3500.0),
            ('35,000', 35000.0),
            ('35.000', 35000.0),
            ('1,00,000', 100000.0),
            ('1.00.000', 100000.0),
            ('R30,700', 30700.0),
            ('F4000', 4000.0),
            ('?4,000.00', 4000.0),
            ('4,900', 4900.0),
            ('4900.00', 4900.0),
            ('Three Thousand Five Hundred Rupees', 3500.0),
            ('Rupees Thirty Thousand Seven Hundred Only', 30700.0),
            ('₹600', 600.0),
            ('600', 600.0),
        ]
        for raw, expected in test_amounts:
            self.assertEqual(parse_amount(raw), expected, f"Failed for {raw}")

if __name__ == '__main__':
    unittest.main()
