import math
import unittest

from backend.app.services.forward_observation import GeoReference,array_location,fold_relative,initial_bearing,observe


class ForwardObservationTests(unittest.TestCase):
    def setUp(self):
        self.reference=GeoReference(30,122)
        self.platform={'start_ll':[30.0623,122.0415],'heading_deg':32.096954843827305,
                       'speed_kn':3.,'hull_m':[0,0],'tow_m':[-150,0]}

    def test_projection_roundtrip_and_invalid_values(self):
        for ll in ((30,122),(30.0623,122.0415),(29.9,121.8)):
            back=self.reference.to_ll(self.reference.to_xy(ll))
            for a,b in zip(ll,back):self.assertAlmostEqual(a,b,places=11)
        for ll in ((float('nan'),122),(91,122),(30,181)):
            with self.assertRaises(ValueError):self.reference.to_xy(ll)
        with self.assertRaises(ValueError):GeoReference(89,122)
        with self.assertRaises(ValueError):GeoReference(30,122,0)

    def test_bearing_and_folding_at_axes(self):
        self.assertAlmostEqual(initial_bearing([0,0],[1,0]),0)
        self.assertAlmostEqual(initial_bearing([0,0],[0,1]),90)
        self.assertAlmostEqual(initial_bearing([0,0],[-1,0]),180)
        self.assertEqual([fold_relative(a) for a in (-90,0,90,180,181,359,360)],
                         [90,0,90,180,179,1,0])
        with self.assertRaises(ValueError):initial_bearing([30,122],[30,122])
        with self.assertRaises(ValueError):fold_relative(float('inf'))

    def test_platform_rotation_motion_and_port_offset(self):
        p={**self.platform,'start_ll':[30,122],'heading_deg':90,'speed_kn':3600/1852}
        xy=self.reference.to_xy(array_location(p,[5,7],10,self.reference))
        self.assertAlmostEqual(xy[0],15,places=6);self.assertAlmostEqual(xy[1],7,places=6)
        with self.assertRaises(ValueError):array_location(p,[0,0],-1,self.reference)

    def test_fixed_generator_fixture_positions_and_spherical_observation(self):
        # Independent explicit rotation, in the generator's reference plane.
        p=self.platform;start=self.reference.to_xy(p['start_ll']);heading=math.radians(p['heading_deg'])
        for time in (0,123.4,600):
            result=observe(p,[30.036178031298103,122.1246451095109],time,self.reference)
            for array in ('hull','tow'):
                forward=p['speed_kn']*1852/3600*time+p[array+'_m'][0]
                east=start[0]+forward*math.sin(heading)-p[array+'_m'][1]*math.cos(heading)
                north=start[1]+forward*math.cos(heading)+p[array+'_m'][1]*math.sin(heading)
                expected=self.reference.to_ll((east,north))
                self.assertAlmostEqual(result[array+'_ll'][0],expected[0],places=11)
                self.assertAlmostEqual(result[array+'_ll'][1],expected[1],places=11)
                self.assertTrue(0<=result[array+'_deg']<=180)
        # Same platform/heading/target as the documented synthetic sample at t=0.
        result=observe(p,[30.036178031298103,122.1246451095109],0,self.reference)
        self.assertAlmostEqual(result['hull_deg'],77.83090603543276,places=8)
        self.assertAlmostEqual(result['tow_deg'],76.8475367247902,places=8)
