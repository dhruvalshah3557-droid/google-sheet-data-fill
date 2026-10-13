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

if __name__=='__main__':unittest.main()
