"""Exercise the approved layouts through the real Qt demo controllers."""
import os
from pathlib import Path
import sys
import unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_CONTROLS_STYLE','Basic')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QObject,QPointF,QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine,QQmlExpression,QQmlEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from rogcontrol.qt.workers import TaskRunner
from rogcontrol.qt.controllers.app import AppController
from rogcontrol.qt.controllers.overview import OverviewController
from rogcontrol.qt.controllers.quick_access import QuickAccessController
from rogcontrol.qt.controllers.cpu import CpuController
from rogcontrol.qt.controllers.gpu import GpuController
from rogcontrol.qt.controllers.fans import FansController
from rogcontrol.qt.controllers.profile import ProfileController
from rogcontrol.qt.controllers.battery import BatteryController
from rogcontrol.qt.controllers.keyboard import KeyboardController
from rogcontrol.qt.controllers.system import SystemController
from rogcontrol.qt.controllers.updates import UpdatesController
ROOT=Path(__file__).resolve().parents[1]

def visual_items(item):
    yield item
    for child in item.childItems():
        yield from visual_items(child)

class WideLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt=QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.runner=TaskRunner()
        self.app=AppController(self.runner,demo=True)
        self.overview=OverviewController(self.app)
        self.quick=QuickAccessController(self.app)
        self.cpu=CpuController(self.app);self.gpu=GpuController(self.app);self.fans=FansController(self.app)
        self.app.tuningPages={'cpu':self.cpu,'gpu':self.gpu,'fans':self.fans}
        self.profiles=ProfileController(self.app,self.app.tuningPages);self.app.profilesController=self.profiles
        self.battery=BatteryController(self.app);self.keyboard=KeyboardController(self.app)
        self.system=SystemController(self.app);self.updates=UpdatesController(self.app)
        self.controllers=[self.overview,self.quick,self.cpu,self.gpu,self.fans,self.profiles,self.battery,self.keyboard,self.system,self.updates]
        for controller in [self.cpu,self.gpu,self.fans]:controller.refresh()
        self.engine=QQmlApplicationEngine();self.warnings=[]
        self.engine.warnings.connect(lambda values:self.warnings.extend(w.toString() for w in values))
        objects={'appController':self.app,'overviewController':self.overview,'quickAccessController':self.quick,'cpuController':self.cpu,'gpuController':self.gpu,'fansController':self.fans,'profileController':self.profiles,'batteryController':self.battery,'keyboardController':self.keyboard,'systemController':self.system,'updatesController':self.updates}
        for key,value in objects.items():self.engine.rootContext().setContextProperty(key,value)
        self.engine.load(QUrl.fromLocalFile(str(ROOT/'rogcontrol/qml/Main.qml')))
        self.assertTrue(self.engine.rootObjects(),self.warnings)
        self.window=self.engine.rootObjects()[0];self.window.resize(1600,1000);self.window.show();QTest.qWait(40)

    def tearDown(self):
        self.app.close()
        for c in self.controllers:c.close()
        self.runner.shutdown()
        self.window.hide();self.engine.deleteLater();self.qt.processEvents()

    def page(self,name):
        self.app.navigate(name)
        QTest.qWait(40)
        prefix={'quick_access':'QuickAccessPage','cpu':'CpuPage','gpu':'GpuPage','fans':'FansPage','keyboard':'KeyboardPage','system':'SystemPage'}[name]
        return next(i for i in visual_items(self.window.contentItem()) if i.metaObject().className().startswith(prefix+'_'))

    def viewport(self):
        return max((i for i in visual_items(self.window.contentItem()) if i.metaObject().className().startswith('ScrollView_')),key=lambda i:i.height())

    def assert_fits_vertically(self,name):
        v=self.viewport()
        self.assertLessEqual(v.property('contentHeight'),v.property('availableHeight')+1,name)

    def test_all_wide_pages_and_existing_subtabs_fit_without_scrolling(self):
        for page in ['quick_access','cpu','gpu','fans','keyboard','system']:
            root=self.page(page)
            if page=='keyboard':
                for mode in self.keyboard.data['modes']:
                    self.keyboard.setLighting('mode',mode);QTest.qWait(25);self.assert_fits_vertically(f'keyboard/{mode}')
            self.assert_fits_vertically(page)
            sections=[1,0] if page=='keyboard' else ['Services','Updates','Diagnostics','General'] if page=='system' else []
            for section in sections:
                root.setProperty('section',section);QTest.qWait(30);self.assert_fits_vertically(f'{page}/{section}')
        self.assertEqual(self.warnings,[])

    def test_profile_buttons_fit_inside_the_clipped_viewport(self):
        for width in [1600, 760, 360]:
            self.window.resize(width,1000);QTest.qWait(40)
            buttons=[i for i in visual_items(self.window.contentItem()) if i.property('modelData') in self.app.profileNames and i.metaObject().className().startswith('Button_')]
            self.assertTrue(buttons)
            for button in buttons:
                ancestor=button.parentItem()
                while ancestor:
                    if ancestor.clip():
                        point=button.mapToItem(ancestor,QPointF(0,0))
                        self.assertGreaterEqual(point.y(),0)
                        self.assertLessEqual(point.y()+button.height(),ancestor.height()+.5)
                    ancestor=ancestor.parentItem()

    def test_save_status_is_deduplicated_in_one_dismissible_notification(self):
        root=self.page('gpu')
        self.app._status='Saved';self.app.stateChanged.emit()
        self.gpu._status='GPU tuning capabilities refreshed';self.gpu.stateChanged.emit()
        QTest.qWait(40)
        status=self.window.findChild(QObject,'statusNotification')
        self.assertIsNotNone(status)
        self.assertIn('Saved',status.property('message'))
        self.assertIn('GPU tuning capabilities refreshed',status.property('message'))
        self.page('keyboard');self.keyboard._status='Saved';self.keyboard.stateChanged.emit();QTest.qWait(40)
        self.assertEqual(status.property('message').count('Saved'),1)
        rendered=[i for i in visual_items(self.window.contentItem()) if i.isVisible() and i.property('text')=='Saved']
        self.assertEqual(len(rendered),1)
        status.setProperty('dismissedMessage',status.property('message'));QTest.qWait(20)
        self.assertFalse(status.isVisible())

    def test_notification_expires_even_when_controller_status_persists(self):
        self.page('keyboard')
        self.keyboard._status='Saved';self.keyboard.stateChanged.emit();QTest.qWait(40)
        notification=self.window.findChild(QObject,'statusNotification')
        self.assertTrue(notification.isVisible())
        QTest.qWait(1750)
        self.assertTrue(notification.isVisible())
        QTest.qWait(350)
        self.assertFalse(notification.isVisible())
        self.assertEqual(self.keyboard.status,'Saved')
        self.keyboard._status='Lighting refreshed';self.keyboard.stateChanged.emit();QTest.qWait(40)
        self.assertTrue(notification.isVisible())

    def test_gpu_display_power_card_fits_its_column(self):
        root=self.page('gpu');card=root.findChild(QObject,'nvidiaDisplayPowerCard')
        self.assertIsNotNone(card)
        self.assertLess(card.width(),root.width()*.65)
        self.assertGreater(card.mapToScene(QPointF(0,0)).x(),root.mapToScene(QPointF(root.width()/2,0)).x())

    def test_narrow_pages_keep_all_cards_inside_window(self):
        self.window.resize(360,860);QTest.qWait(40)
        for name in ['quick_access','cpu','gpu','fans','keyboard','system']:
            self.page(name)
            for item in visual_items(self.window.contentItem()):
                if item.isVisible() and item.metaObject().className().startswith('MetricCard_'):
                    p=item.mapToScene(QPointF(0,0))
                    self.assertGreaterEqual(p.x(),-1,name)
                    self.assertLessEqual(p.x()+item.width(),self.window.width()+1,(name,item.property('title')))
        self.assertEqual(self.warnings,[])

    def test_rog_layout_keeps_hotkeys_and_full_size_arrows_inside_the_deck(self):
        import json
        source=(ROOT/'rogcontrol/qml/components/RogKeyboardLayout.js').read_text()
        keys=json.loads(source.split('var keys = ',1)[1])
        self.assertEqual(len([k for k in keys if k.get('macro')]),5)
        for key in keys:
            self.assertGreaterEqual(key['x'],0)
            self.assertLessEqual(key['x']+key['w'],16)
            self.assertGreaterEqual(key['y'],0)
            self.assertLessEqual(key['y']+key['h'],7)
        arrows={k['label']:k for k in keys if k['label'] in ['←','↑','↓','→']}
        self.assertEqual(set(arrows),{'←','↑','↓','→'})
        self.assertTrue(all(k['w']==1 for k in arrows.values()))
        self.assertEqual(arrows['↑']['x'],arrows['↓']['x'])
        self.assertLess(arrows['↑']['y'],arrows['↓']['y'])
        for i,a in enumerate(keys):
            for b in keys[i+1:]:
                overlaps=(a['x']<b['x']+b['w']-.01 and a['x']+a['w']>b['x']+.01
                          and a['y']<b['y']+b['h']-.01 and a['y']+a['h']>b['y']+.01)
                self.assertFalse(overlaps,(a['label'],b['label']))

    def test_keyboard_preview_reacts_to_lighting_and_pauses_when_hidden(self):
        self.page('keyboard')
        preview=self.window.findChild(QObject,'keyboardPreview')
        self.assertIsNotNone(preview)
        self.keyboard.setLighting('mode','Static');self.keyboard.setLighting('color1','#ff4000');QTest.qWait(40)
        colour,_=QQmlExpression(QQmlEngine.contextForObject(preview),preview,'zoneColours[0]').evaluate()
        self.assertEqual(colour.name(),'#ff4000')
        self.keyboard.setLighting('brightness',0);QTest.qWait(30)
        self.assertEqual(preview.property('ledOpacity'),0)
        self.assertFalse(preview.property('animationRunning'))
        self.keyboard.setLighting('brightness',3);self.keyboard.setLighting('mode','Rainbow');self.keyboard.setLighting('speed',1);QTest.qWait(30)
        self.assertTrue(preview.property('animationRunning'))
        self.assertEqual(preview.property('animationDuration'),3600)
        self.keyboard.setLighting('speed',3);QTest.qWait(30)
        self.assertEqual(preview.property('animationDuration'),1200)
        self.window.hide();QTest.qWait(30)
        self.assertFalse(preview.property('animationRunning'))
        self.assertEqual(self.warnings,[])

if __name__=='__main__':unittest.main()
