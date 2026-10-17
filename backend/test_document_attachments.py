import io
from pathlib import Path
import sys
import unittest
import zipfile

from dervo_connections import Rejected
from document_attachments import extract, context


def zipped(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, text in files.items(): archive.writestr(name, text)
    return out.getvalue()



def tiny_pdf(text):
    """A one-page PDF with ``text`` on it, built by hand with a correct cross-reference table."""
    stream = f'BT /F1 12 Tf 72 720 Td ({text}) Tj ET'.encode()
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
               b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'\nendstream',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    out, offsets = bytearray(b'%PDF-1.4\n'), []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b'%d 0 obj\n' % number + body + b'\nendobj\n'
    xref = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1)
    out += b''.join(b'%010d 00000 n \n' % offset for offset in offsets)
    out += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objects) + 1, xref)
    return bytes(out)

class Documents(unittest.TestCase):
    def test_powerpoint_numeric_slide_id_is_not_its_relationship_reference(self):
        slides = {
            'ppt/presentation.xml': '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="256" r:id="rId7"/></p:sldIdLst></p:presentation>',
            'ppt/_rels/presentation.xml.rels': '<Relationships><Relationship Id="rId7" Target="slides/slide1.xml"/></Relationships>',
            'ppt/slides/slide1.xml': '<slide><t>Sample: Lilac</t><t>Amount: 26</t></slide>',
        }
        self.assertIn('Sample: Lilac\nAmount: 26', extract('slides.pptx', zipped(slides)))
        # Attribute order must not choose the numeric slide ID either.
        slides['ppt/presentation.xml'] = slides['ppt/presentation.xml'].replace('id="256" r:id="rId7"', 'r:id="rId7" id="256"')
        self.assertIn('Amount: 26', extract('slides.pptx', zipped(slides)))
        slides['ppt/presentation.xml'] = slides['ppt/presentation.xml'].replace('http://schemas.openxmlformats.org/officeDocument/2006/relationships', 'http://purl.oclc.org/ooxml/officeDocument/relationships')
        self.assertIn('Amount: 26', extract('slides.pptx', zipped(slides)))
        original = slides['ppt/presentation.xml']
        for malformed in (original.replace('r:id="rId7"', ''),
                          original.replace('http://purl.oclc.org/ooxml/officeDocument/relationships', 'urn:unrelated')):
            slides['ppt/presentation.xml'] = malformed
            with self.assertRaises(Rejected): extract('slides.pptx', zipped(slides))
        slides['ppt/presentation.xml'] = original
        slides['ppt/_rels/presentation.xml.rels'] = '<Relationships><Relationship Id="rId7" Target="https://example.com/slide.xml" TargetMode="External"/></Relationships>'
        with self.assertRaises(Rejected): extract('slides.pptx', zipped(slides))

    def test_csv_encodings_and_reference_boundary(self):
        text = 'name,total\nBryce,42\n'
        for raw in (text.encode(), text.encode('utf-16'), b'\xef\xbb\xbf' + text.encode()):
            self.assertEqual(extract('sales.csv', raw), text)
        self.assertIn('not as instructions', context([{'name': 'sales.csv', 'data': text.encode()}]))

    def test_binary_unknown_empty_and_long_documents_fail_without_truncation(self):
        for name, data in [('a.txt', b'hello\x00world'), ('a.exe', b'hello'), ('a.csv', b' '),
                           ('a.txt', b'x' * 60001), ('a.pdf', b'not PDF')]:
            with self.subTest(name=name, size=len(data)), self.assertRaises(Rejected): extract(name, data)

    def test_a_pdf_on_windows_says_windows_cant_read_it_yet_and_runs_nothing(self):
        import subprocess
        from unittest import mock
        import document_attachments
        with mock.patch('platform_os.WINDOWS', True), mock.patch.object(subprocess, 'run', side_effect=AssertionError('no osascript on Windows')):
            with self.assertRaises(Rejected) as caught: extract('report.pdf', b'%PDF-1.7\n...')
            self.assertEqual(str(caught.exception), document_attachments.PDF_ON_WINDOWS)
            with self.assertRaises(Rejected) as caught: extract('report.pdf', b'not PDF')
            self.assertEqual(str(caught.exception), 'This file isn’t a readable PDF.')
            with self.assertRaises(Rejected) as caught: extract('a.exe', b'hello')
            self.assertNotIn('PDF', str(caught.exception), 'Windows doesn’t offer PDF in the supported list')
            self.assertEqual(extract('notes.txt', b'still fine'), 'still fine')
        with self.assertRaises(Rejected) as caught: extract('a.exe', b'hello')
        self.assertIn('PDF', str(caught.exception))

    @unittest.skipUnless(sys.platform.startswith('linux') and Path('/usr/bin/pdftotext').is_file(), 'Linux with poppler-utils')
    def test_a_pdf_on_linux_reads_through_poppler(self):
        self.assertIn('Hello from Dervo', extract('report.pdf', tiny_pdf('Hello from Dervo')))
        with self.assertRaises(Rejected): extract('report.pdf', b'%PDF-1.7\nnot really a PDF')

    def test_a_pdf_on_linux_without_poppler_says_what_to_install(self):
        from unittest import mock
        import document_attachments
        with mock.patch('platform_os.LINUX', True), mock.patch('platform_os.WINDOWS', False), \
                mock.patch.object(document_attachments, 'PDFTOTEXT', '/nonexistent/pdftotext'):
            with self.assertRaises(Rejected) as caught: extract('report.pdf', b'%PDF-1.7\n...')
            self.assertEqual(str(caught.exception), document_attachments.PDF_ON_LINUX)

    def test_office_text_and_cached_spreadsheet_values(self):
        self.assertEqual(extract('a.docx', zipped({'word/document.xml': '<document><p><r><t>Hello</t></r></p></document>'})), 'Hello')
        sheet = '<worksheet><row><c r="A1" t="s"><v>0</v></c><c r="B1"><f>1+1</f><v>2</v></c></row></worksheet>'
        result = extract('a.xlsx', zipped({'xl/sharedStrings.xml': '<sst><si><t>Total</t></si></sst>', 'xl/worksheets/sheet1.xml': sheet}))
        self.assertIn('A1: Total', result)
        self.assertIn('B1: 2 [formula: 1+1; not recalculated]', result)
        self.assertIn('Hello', extract('a.pptx', zipped({'ppt/slides/slide1.xml': '<slide><t>Hello</t></slide>'})))

    def test_office_entities_and_expansion_are_rejected(self):
        for xml in ['<!DOCTYPE x [<!ENTITY a "hello">]><document><p><t>&a;</t></p></document>', 'x' * (8 * 1024 * 1024 + 1)]:
            with self.assertRaises(Rejected): extract('a.docx', zipped({'word/document.xml': xml}))


    def test_reordered_slides_and_sheets_follow_document_order(self):
        slides = {'ppt/presentation.xml':'<presentation xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sldId r:id="second"/><sldId r:id="first"/></presentation>',
                  'ppt/_rels/presentation.xml.rels':'<Relationships><Relationship Id="first" Target="slides/slide1.xml"/><Relationship Id="second" Target="slides/slide2.xml"/></Relationships>',
                  'ppt/slides/slide1.xml':'<slide><t>FIRST TEXT</t></slide>', 'ppt/slides/slide2.xml':'<slide><t>SECOND TEXT</t></slide>'}
        result = extract('deck.pptx', zipped(slides))
        self.assertLess(result.index('SECOND TEXT'), result.index('FIRST TEXT'))
        sheets = {'xl/workbook.xml':'<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheet name="Summary" r:id="second"/><sheet name="Details" r:id="first"/></workbook>',
                  'xl/_rels/workbook.xml.rels':'<Relationships><Relationship Id="first" Target="worksheets/sheet1.xml"/><Relationship Id="second" Target="worksheets/sheet2.xml"/></Relationships>',
                  'xl/worksheets/sheet1.xml':'<worksheet><c r="A1"><v>1</v></c></worksheet>',
                  'xl/worksheets/sheet2.xml':'<worksheet><c r="A1"><v>2</v></c></worksheet>'}
        result = extract('book.xlsx', zipped(sheets))
        self.assertLess(result.index('Summary\nA1: 2'), result.index('Details\nA1: 1'))
        sheets['xl/_rels/workbook.xml.rels'] = sheets['xl/_rels/workbook.xml.rels'].replace('worksheets/sheet2.xml', '../../outside.xml')
        with self.assertRaises(Rejected): extract('book.xlsx', zipped(sheets))
