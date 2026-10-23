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
    def test_duplicate_headers_block_only_ambiguous_fields(self):
        snapshot=[{'STK':'1','PRODUCT NAME':'','turkish description':''}]
        live=[['STK','PRODUCT NAME','turkish description','turkish description'],['1','','','']]
        self.assertEqual(select_safe_updates('diamond stock',snapshot,live,[(2,2,'Title'),(2,3,'text')]),[(2,2,'Title')])
    def test_confirmed_ftp_alias_is_allowed(self):
        data=[['STK','model video link 1'],['1333_6674_6673','https://colourdiam.com/Product/Model%20Photo%20Video/1333_6674/video.mp4']]
        self.assertEqual(audit('jewellery stock',data,{'1333_6674_6673':'1333_6674'}),[])
    def test_semantic_headers(self):
        findings=audit('jewellery stock',[['STK','Metal Colour','GIA information','Shipping','Packaging'],['1','18K','2026-10-03T20:00:00Z','#diamond','2026-10-03 20:00:00']])
        self.assertEqual(sum(x['issue']=='content does not match header' for x in findings),3)
        self.assertIn('metal purity stored as colour',{x['issue'] for x in findings})
        self.assertFalse(is_marketing_header('Metal Colour'))
if __name__=='__main__':unittest.main()


class HashtagQualityTests(unittest.TestCase):
    def test_native_combining_marks_are_kept(self):
        from hashtag_quality import tokens
        self.assertEqual(tokens('#เครื่องประดับ #ನೈಸರ್ಗಿಕವಜ್ರ #తెలుగు'), ['เครื่องประดับ','ನೈಸರ್ಗಿಕವಜ್ರ','తెలుగు'])
    def test_order_independent_sets_and_stock_only_variation(self):
        from hashtag_quality import audit_hashtags
        data=[['STK','Hashtags'],['1','#ColourDiam #OvalDiamond #Stock1'],['2','#Stock2 #OvalDiamond #ColourDiam'],['3','#Stock2 #ColourDiam #OvalDiamond']]
        issues={x['issue'] for x in audit_hashtags('diamond stock',data)}
        self.assertIn('repeated full hashtag set',issues)
        self.assertIn('hashtag template differs only by stock reference',issues)
    def test_duplicates_language_and_ambiguous_target(self):
        from hashtag_quality import cell_issues
        self.assertIn('duplicate hashtag inside cell',cell_issues('Hashtags','#Oval #oval'))
        self.assertIn('hashtag language script mismatch',cell_issues('thai hashtag','#Diamond #Stock12'))
        self.assertIn('generic-only hashtag set',cell_issues('Hashtags','#ColourDiam #Diamond #Stock123'))
        self.assertEqual(cell_issues('vestslavic hashtag','#Diamond'),['ambiguous hashtag language'])
    def test_curated_tag_is_preserved(self):
        data=[{'STK':'1','Hashtags':''}];live=[['STK','Hashtags'],['1','#Curated #OvalDiamond']]
        self.assertEqual(select_safe_updates('diamond stock',data,live,[(2,2,'#Replacement')],True),[])

    def test_japanese_kanji_are_valid(self):
        from hashtag_quality import cell_issues
        self.assertNotIn('hashtag language script mismatch',cell_issues('Japanese Hashtag','#宝石 #天然石'))
    def test_filler_synonym_and_invented_tags_are_rejected(self):
        from hashtag_quality import cell_issues
        self.assertIn('duplicate hashtag inside cell',cell_issues('Hashtags','#Ring #DiamondRing #ColourDiam'))
        self.assertNotIn('duplicate hashtag inside cell',cell_issues('Hashtags','#AGL #AGL23061119 #Ring'))
        self.assertIn('hashtag not relevant to product',cell_issues('Hashtags','#ColourDiam #ColourStory #JewelleryLovers'))
        self.assertIn('hashtag not supported by product facts',cell_issues('Hashtags','#ColourDiam #LabGrownDiamond #AsscherCut'))
