import unittest
from integrity_guard import select_safe_updates, audit, is_marketing_header

class IntegrityTests(unittest.TestCase):
    def test_keyed_write_after_row_and_header_reorder(self):
        snapshot=[{'STK':'1','PRODUCT NAME':'','PRICE':10},{'STK':'2','PRODUCT NAME':'','PRICE':20}]
        live=[['PRICE','PRODUCT NAME','STK'],[20,'','2'],[10,'','1']]
        self.assertEqual(select_safe_updates('diamond stock ',snapshot,live,[(2,2,'Unique title')],True),[(3,2,'Unique title')])
    def test_preserve_copy_source_formula_and_ambiguous_keys(self):
        snapshot=[{'STK':'1','PRODUCT NAME':'','PRICE':10}]
        live=[['STK','PRODUCT NAME','PRICE'],['1','Curated',10]]
        self.assertEqual(select_safe_updates('diamond stock',snapshot,live,[(2,2,'New'),(2,3,99)],True),[])
        live[1][1]='=FILTER(A:A,A:A<>"")'
        self.assertEqual(select_safe_updates('diamond stock',snapshot,live,[(2,2,'New')]),[])
        live.append(['1','',''])
        self.assertEqual(select_safe_updates('diamond stock',snapshot,live,[(2,2,'New')]),[])
    def test_new_languages_and_source_colour(self):
        for h in ['kannada description','telagu hashtag','malayalam description','turkish description']:self.assertTrue(is_marketing_header(h))
        self.assertFalse(is_marketing_header('colour'))
    def test_mismatched_media_and_duplicate_copy(self):
        findings=audit('diamond stock',[['STK','image1 link','kannada description'],['1','https://colourdiam.com/Product/Diamond/2/center.jpg','Lovely 1.00 diamond'],['3','','Lovely 2.00 diamond']])
        issues={f['issue'] for f in findings}
        self.assertIn('media stock mismatch',issues);self.assertIn('regional script missing',issues);self.assertIn('repeated description structure',issues)
if __name__=='__main__':unittest.main()
