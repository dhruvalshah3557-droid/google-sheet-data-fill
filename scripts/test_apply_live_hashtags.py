import copy
import unittest
from apply_live_hashtags import HEADERS,TABS,validate,preflight

class LiveTests(unittest.TestCase):
    def setUp(self):
        h=['']*149
        h[1]='STK'
        for col,name in HEADERS.items():h[col]=name
        row=['']*149;row[1]='example';row[4]='Oval diamond';row[8]='Diamond';row[47]='#Old'
        self.before={tab:(copy.deepcopy([h,row]),copy.deepcopy([h,row]),copy.deepcopy([h,row])) for tab in TABS}
        self.updates=[(next(iter(TABS)),2,48,'#OvalDiamond #JewelleryDesign')]
    def test_valid(self):
        validate(self.updates,self.before);preflight(self.before,copy.deepcopy(self.before),self.updates)
    def test_source_oldtag_formula_header_abort(self):
        tab=next(iter(TABS))
        for kind in ['source','old','formula','header']:
            after=copy.deepcopy(self.before)
            if kind=='source':after[tab][2][1][4]='Changed'
            if kind=='old':after[tab][0][1][47]='#Changed'
            if kind=='formula':after[tab][1][1][47]='=A1'
            if kind=='header':after[tab][0][0][47]='Changed'
            with self.assertRaises(ValueError):preflight(self.before,after,self.updates)
    def test_column_duplicate_and_language_abort(self):
        tab=next(iter(TABS))
        for updates in [[(tab,2,109,'#OvalDiamond')],self.updates+self.updates,[(tab,2,48,'#Oval #oval')],[(tab,2,79,'#EnglishOnly')]]:
            with self.assertRaises(ValueError):validate(updates,self.before)

class GeneratorTests(unittest.TestCase):
    def generate(self, rows):
        import json, subprocess
        from pathlib import Path
        vocab=Path(__file__).with_name('hashtag_vocabulary.json')
        gen=Path(__file__).with_name('generate_live_hashtags.cjs')
        process=subprocess.run(['node',str(gen),str(vocab)],input=json.dumps({'rows':rows},ensure_ascii=False),text=True,capture_output=True)
        self.assertEqual(process.returncode,0,process.stderr)
        return json.loads(process.stdout)['rows']
    def tags(self, value):
        from hashtag_quality import tokens
        return tokens(value)
    def test_product_facts_only_and_no_filler(self):
        from hashtag_quality import cell_issues
        from apply_live_hashtags import HEADERS
        rows=self.generate([
            {'sheet':'diamond stock ','sheet_id':1,'row':2,'stock':'9468','code':'Fancy Brownish Greenish Yellow','details':'Sku - 9468\nWeight - 6.11\nColour - Fancy Brownish Greenish Yellow\nShape - Pear\nClarity - I1\nMeasurements - 14.66 x 9.19 x 6.31\nFluorescence - Fnt','lab':'GIA','type':'Loose Diamond'},
            {'sheet':'jewellery stock ','sheet_id':2,'row':3,'stock':'298','code':'','details':'2.01 Ct Fancy Intense Yellow I1\n18K - Ring : 3.252 gram\nMain Stone - 1 Pcs 2.01 Cts\nRound WHITE 83 Pcs 0.34 Cts','lab':'GIA','type':'Ring'},
        ])
        diamond={c['column']:c['value'] for c in rows[0]['cells']}
        jewel={c['column']:c['value'] for c in rows[1]['cells']}
        d=self.tags(diamond[47]); j=self.tags(jewel[47])
        self.assertIn('loosediamond',d); self.assertIn('pearcut',d); self.assertIn('yellow',d); self.assertIn('gia',d)
        self.assertNotIn('diamonds',d); self.assertNotIn('labgrowndiamond',d)
        self.assertIn('ring',j); self.assertIn('yellow',j); self.assertIn('18kgold',j); self.assertNotIn('diamondring',j)
        self.assertNotIn('pink',j); self.assertNotIn('pendant',j)
        self.assertTrue(set(c['column'] for c in rows[0]['cells'])==set(HEADERS))
        for col,value in diamond.items():
            self.assertFalse(cell_issues(HEADERS[col],value), (HEADERS[col],value))
        self.assertNotEqual(set(d), set(self.tags(diamond[146])))

if __name__=='__main__':unittest.main()
