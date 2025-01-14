import os
import pytest

from a2ml.api.a2ml import A2ML
from a2ml.api.auger.a2ml import AugerA2ML
from a2ml.api.google.a2ml import GoogleA2ML
from a2ml.api.utils.context import Context

from .utils.mock_helpers import MockHelpers

class TestFacade(object):

    def setup_method(self, method):
        self.cwd = os.getcwd()
        # run test in context of the test app
        os.chdir('tests/test_app')
        # load config(s) from the test app
        self.ctx = Context()

    def teardown_method(self, method):
        os.chdir(self.cwd)

    @pytest.fixture
    def mock_inits(self, monkeypatch):
        MockHelpers.pass_method(AugerA2ML, "__init__", monkeypatch)
        MockHelpers.pass_method(GoogleA2ML, "__init__", monkeypatch)

    def test_init_a2ml(self, monkeypatch):
        init_auger = MockHelpers.count_calls(
            AugerA2ML, "__init__", monkeypatch)
        init_google = MockHelpers.count_calls(
            GoogleA2ML, "__init__", monkeypatch)
        self.ctx.config['config'].providers = 'auger'
        a2ml = A2ML(self.ctx)
        assert len(a2ml.runner.providers) == 1
        assert isinstance(a2ml.runner.providers[0], AugerA2ML)
        assert init_auger.times == 1
        assert init_google.times == 0
        # modify config on the fly
        self.ctx.config['config'].providers = ['auger','google']
        init_auger.reset()
        init_google.reset()
        a2ml = A2ML(self.ctx)
        assert len(a2ml.runner.providers) == 2
        assert isinstance(a2ml.runner.providers[0], AugerA2ML)
        assert isinstance(a2ml.runner.providers[1], GoogleA2ML)
        assert init_auger.times == 1
        assert init_google.times == 1

    def test_calling_operations(self, mock_inits, monkeypatch):
        def test_operation(operation, args):
            auger_operation = MockHelpers.called_with(
                AugerA2ML, operation, monkeypatch)
            google_operation = MockHelpers.called_with(
                GoogleA2ML, operation, monkeypatch)
            #run operation for a single provider
            self.ctx.config['config'].providers = ['auger']
            a2ml = A2ML(self.ctx)
            getattr(a2ml, operation)(*args)
            assert auger_operation.times == 1
            assert google_operation.times == 0
            for arg in range(len(args)):
                assert auger_operation.args[arg+1] == args[arg]
            #run operation for multiple providers
            self.ctx.config['config'].providers = ['auger','google']
            auger_operation.reset()
            google_operation.reset()
            a2ml = A2ML(self.ctx)
            getattr(a2ml, operation)(*args)
            assert auger_operation.times == 1
            assert google_operation.times == 1
            for arg in range(len(args)):
                assert auger_operation.args[arg+1] == args[arg]
                assert google_operation.args[arg+1] == args[arg]
        ops = {
            'import_data': [],
            'train': [],
            'evaluate': [],
            'deploy': ['some_model_id', True],
            'predict': ['some_csv', 'some_model_id', 0.5, True],
            'review': []
        }
        for opname, args in ops.items():
            test_operation(opname,args)
