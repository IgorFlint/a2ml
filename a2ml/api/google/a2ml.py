import os
import json
import csv
import sys
import inspect
from google.cloud import automl_v1beta1 as automl
from google.cloud.automl_v1beta1 import enums
import google.auth
from a2ml.api.utils.config_yaml import ConfigYaml
from google.auth.transport.requests import AuthorizedSession

class GoogleA2ML(object):
    """Google A2ML implementation."""

    def __init__(self,ctx):
        super(GoogleA2ML, self).__init__()
        self.ctx = ctx
        self.client = automl.AutoMlClient()
        self.name = ctx.config['config'].get('name',None)
        self.project_id = ctx.config['google'].get('project',None)
        self.compute_region = ctx.config['google'].get('cluster/region','us-central1')
        self.metric = ctx.config['google'].get('experiment/metric',"MINIMIZE_MAE")
        self.project_location = self.client.location_path(self.project_id,self.compute_region)
        self.dataset_id = ctx.config['google'].get('dataset_id',None)
        self.dataset_name = ctx.config['google'].get('dataset_name',None)
        self.source = ctx.config['config'].get('source', None)
        self.dataset_name = self.client.dataset_path(self.project_id, self.compute_region, self.dataset_id)
        self.target = ctx.config['config'].get('target',None)
        self.exclude = ctx.config['config'].get('exclude',None)
        self.budget = ctx.config['config'].get('budget',None)
        self.operation_name = ctx.config['google'].get('operation_name',None)
        self.model_name = ctx.config['google'].get('model_name',None)
        self.gsbucket = ctx.config['google'].get('gsbucket','gs://a2ml')

    def import_data(self):
        self.ctx.log('Google Import Data')
        self.ctx.log("Creating dataset for project location: {}".format(self.project_location))
        if self.source is None:
            self.ctx.log("Please specify a source (URL or local file)")
            return
            
        create_dataset_response = self.client.create_dataset(
            self.project_location,
            {'display_name': self.name,
            'tables_dataset_metadata': {}})
        self.dataset_id = create_dataset_response.name.split('/')[-1]
        self.ctx.config['google'].yaml['dataset_id'] = self.dataset_id
        self.ctx.config['google'].write()

        print ("Dataset ID: {}".format(self.dataset_id))
        self.dataset_name = self.client.dataset_path(self.project_id, self.compute_region, self.dataset_id)
        self.ctx.log('Dataset name: {}'.format(self.dataset_name))

        if os.path.isfile(self.source):
            print("Copying {} to {}".format(self.source,self.gsbucket))
            cmd = "gsutil cp " + self.source + " "+ self.gsbucket 
            result = os.system(cmd)
            print("Result of local copy to Google Storage bucket: {}".format(result)) 
            self.source = self.gsbucket + "/" + os.path.basename(self.source)

        if self.source.startswith('bq'):
            input_config = {"bigquery_source": {"input_uri": self.source}}
        else:
            # Get the multiple Google Cloud Storage URIs.
            input_uris = self.source.split(",")
            input_config = {"gcs_source": {"input_uris": input_uris}}

        self.ctx.log("Processing import for dataset ID: {}".format(self.dataset_id))

        operation= self.client.import_data(self.dataset_name, input_config)
        # synchronous check of operation status.
        import_data_response = operation.result()
        self.ctx.log("Imported data: {}".format(import_data_response))
        
        return self

    def train(self,synchronous=False):
        self.ctx.log('Google Train')
        self.ctx.log("Training model: {}".format(self.name))
        
        self.ctx.log("Listing tables from: {}".format(self.dataset_name))
        list_table_specs_response = self.client.list_table_specs(self.dataset_name)
        #self.ctx.log("List table specs response: {}".format(list_table_specs_response))
        table_specs = [s for s in list_table_specs_response]
        table_spec_name = table_specs[0].name
        self.ctx.log("Table spec name: {}".format(table_spec_name))
        
        list_column_specs_response = self.client.list_column_specs(table_spec_name)
        self.column_specs = {s.display_name: s for s in list_column_specs_response}

        label_column_name = self.target
        label_column_spec = self.column_specs[label_column_name]
        label_column_id = label_column_spec.name.rsplit('/', 1)[-1]
        update_dataset_dict = {
            'name': self.dataset_name, 
            'tables_dataset_metadata': {'target_column_spec_id': label_column_id}}
        update_dataset_response = self.client.update_dataset(update_dataset_dict)
        self.ctx.log("Updated dataset response: {}".format(update_dataset_response))
        self.feat_list = list(self.column_specs.keys())
        self.feat_list.remove(self.target)
        excluded = self.exclude.split(',')
        for exclude in excluded:
            self.ctx.log("Removing: {}".format(exclude))
            try: 
                self.feat_list.remove(exclude)
            except Exception as inst:
                self.ctx.log("Can't find: {}: {}".format(exclude,inst))

        model_dict = {
        'display_name': self.name,
        'dataset_id': self.dataset_name.rsplit('/',1)[-1],
        'tables_model_metadata': {
            'target_column_spec': self.column_specs[self.target],
            'input_feature_column_specs': [
                self.column_specs[x] for x in self.feat_list],
            'train_budget_milli_node_hours': self.budget*60*1000, # budget is in minutes, google wants "millihours", seriously?
            'optimization_objective': self.metric}}
        response = self.client.create_model(self.project_location,model_dict)

        self.operation_name = response.operation.name
        self.ctx.log("Training operation name: {}".format(self.operation_name))
        self.ctx.config['google'].yaml['operation_name']=self.operation_name

        if synchronous: 
            model_response=response.result()
            metadata = model_response.metadata()
            self.ctx.log("Training completed: {}".format(metadata))
            model_name = model_response.name()
            self.ctx.log("Model name: {}".format(model_name))

        self.ctx.config['google'].write()

    def evaluate(self):
        credentials, project = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
        authed_session = AuthorizedSession(credentials)
        basename="https://automl.googleapis.com/v1beta1/"
        cmd = basename + self.operation_name
        response=authed_session.get(cmd)
        result=json.loads(response.content)
        self.ctx.log("Operation name: {}".format(result["name"]))
     
        if (("done" in result.keys()) and result["done"]): 
            self.ctx.log("Model training complete.")
            self.model_name = result["response"]["name"]
            self.ctx.log("Model full name: {}".format(self.model_name))   
            self.ctx.config['google'].yaml['model_name'] = self.model_name
            self.ctx.config['google'].write()  
            response = self.client.list_model_evaluations(self.model_name)
            self.ctx.log("List of model evaluations:")
            for evaluation in response:
                self.ctx.log("Model evaluation name: {}".format(evaluation.name))
                self.ctx.log("Model evaluation id: {}".format(evaluation.name.split("/")[-1]))
                self.ctx.log("Model evaluation example count: {}".format(
                    evaluation.evaluated_example_count))
                self.ctx.log("Model evaluation time: {} seconds".format(evaluation.create_time.seconds))
                self.ctx.log("Full model evaluation: {}".format(inspect.getmembers(evaluation) ))
                self.ctx.log("\n")
        else:
            self.ctx.log("Model still training...")

    def deploy(self, model_id, locally=False):
        self.ctx.log('Google Deploy'.format(model_id))
        if (model_id is None):
            model_name = self.model_name
        else:
            model_name = self.client.model_path(self.project_id, self.compute_region, self.id)
        try: 
            self.ctx.log('Deploy model: {}'.format(self.model_name))
            response = self.client.deploy_model(self.model_name)
            self.ctx.log("Deploy result: {}".format(response))
        except google.api_core.exceptions.FailedPrecondition as inst:
            self.ctx.log("Failed to deploy because its already deploying: {}...".format(inst))

    def predict(self, filename, model_id, threshold=None, locally=False):
        self.ctx.log('Google Predict')
        prediction_client = automl.PredictionServiceClient()
        predictions_file = filename.split('.')[0]+'_results.csv' 
        predictions=open(predictions_file, "wt")  
        with open(filename,"rt") as csv_file:
            content = csv.reader(csv_file)
            csvlist = ''
            for row in content:
                # Create payload
                values = []
                for column in row:
                    self.ctx.log("Column: {}".format(column))
                    values.append({'number_value': float(column)})
                csvlist=",".join(row)
                print ("CSVList: {}".format(csvlist))
                payload = {
                    'row': {'values': values}
                }
                response = prediction_client.predict(self.model_name, payload)
                self.ctx.log("Prediction results:")
                for result in response.payload:
                    if ((threshold is None) or (result.classification.score >= score_threshold)): 
                        prediction=result.tables.value.number_value
                self.ctx.log("Prediction: {}".format(prediction))
                csvlist += (',' + str(prediction) + '\n')
                predictions.write(csvlist) 

    def review(self):
        self.ctx.log('Google Review')

        # Get complete detail of the model.
        model = self.client.get_model(self.model_name)

        # Retrieve deployment state.
        if model.deployment_state == enums.Model.DeploymentState.DEPLOYED:
            deployment_state = "deployed"
        else:
            deployment_state = "undeployed"

        # Display the model information.
        self.ctx.log("Model name: {}".format(self.model_name))
        self.ctx.log("Model id: {}".format(model.name.split("/")[-1]))
        self.ctx.log("Model display name: {}".format(model.display_name))
        self.ctx.log("Model metadata:")
        self.ctx.log(model.tables_model_metadata)
        self.ctx.log("Model create time (seconds): {}".format(model.create_time.seconds))
        self.ctx.log("Model deployment state: {}".format(deployment_state))
