import numpy as np
import torch
from torch import nn, optim
import argparse
from privacy_meter.audit import Audit, MetricEnum
from privacy_meter.audit_report import ROCCurveReport, SignalHistogramReport
from privacy_meter.constants import InferenceGame
from privacy_meter.dataset import Dataset
from privacy_meter.information_source import InformationSource
import torch
import yaml
import seaborn as sns
import torch
import torchvision
import pandas as pd
import logging
from sklearn.model_selection import train_test_split
import time
import copy
from models import get_model
import collections
from dataset import get_dataset,get_cifar10_subset
from pathlib import Path
import pickle
import os
from privacy_meter.model import PytorchModelTensor
from privacy_meter import audit_report
from train import train
import matplotlib.pyplot as plt
from util import get_split


logging.basicConfig(filename='log_time.log',
                    filemode='a',)
logging.getLogger().setLevel(logging.INFO)



def prepare_datasets(dataset_size,num_target_model,configs,model_metadata_list,matched_idx=None):         
    """Prepare the dataset for training the target models when the training data are sampled uniformly from the distribution (pool of all possible data).

    Args:
        dataset_size (int): Size of the whole dataset
        num_target_model (int): Number of target models for auditing
        configs (dict): Data split configuration
        model_metadata_list (dict): Model metedata dict
        matched_idx (list, optional): Idx list of existing target model which matches the requirement. Defaults to None.

    Raises:
        ValueError: _description_
        ValueError: _description_

    Returns:
        dataset_splits: Data split information which saves the information of training points index and test points index for all target models.
    """ 
    
    # The index_list will save all the information about the train, test and auit for each target model.
    index_list = [] 
    all_index = np.arange(dataset_size)
    
    # Load the datasets for the existing target models
    if matched_idx is not None: 
        for metadata_idx in matched_idx:
            metadata = model_metadata_list['model_metadata'][metadata_idx]
            
            # Check if the existing target data has the test split; If yes, we will directly load the splitted test and audit data index list. # If not, we will add the test and audit data for it.
            if 'test_split' in metadata: 
                index_list.append({'train':metadata['train_split'],'test':metadata['test_split'],'audit':metadata['audit_split']})
            else: 
                rest_index = np.array([i for i in all_index if i not in metadata['train_split']])
                test_index = np.random.choice(rest_index,int(configs['f_test']*dataset_size),replace=False)
                used_index= np.concatenate([metadata['train_split'],test_index])
                audit_index = get_split(all_index,used_index,size=int(configs['f_audit']*dataset_size),split_method=configs['split_method'])
                index_list.append({'train':metadata['train_split'],'test':test_index,'audit':audit_index})
 
    # Construct the datasets for additional models 
    num_addition_dataset = num_target_model - len(index_list)
    for split in range(num_addition_dataset):
        selected_index = np.random.choice(all_index,int((configs['f_train']+configs['f_test'])*dataset_size),replace=False)
        train_index, test_index = train_test_split(selected_index, test_size=configs['f_test']/(configs['f_train']+configs['f_test']))
        audit_index = get_split(all_index,selected_index,size=int(configs['f_audit']*dataset_size), split_method = configs['split_method'])    
        index_list.append({'train':train_index,'test':test_index,'audit':audit_index})
    
    dataset_splits = {'split':index_list,'split_method':configs['split_method']}
    return dataset_splits



def prepare_datasets_for_sample_privacy_risk(N,num_total,num_models,data_idx,configs,data_type,model_metadata_list,matched_in_idx=None):        
    """Prepare the datasets for auditing the priavcy risk for a data point. We prepare the dataset with or without the target point for training a set of models with or without the target point.

    Args:
        N (int): Size of the whole dataset 
        num_total (int): Number of all target models 
        num_models (int): Number of additional target models 
        data_idx (int): Data index of the target point
        configs (dict): Data split configuration
        data_type (str): Indicate whether we want to include the target point or exclude the data point (takes value from 'include' and 'exclude' )
        model_metadata_list (dict): Metadata for existing models. 
        matched_in_idx (list, optional): _description_. Defaults to None.

    Returns:
        _type_: _description_
    """
    associated_models = []    
    all_index = np.arange(N)
    all_index_exclude_z = np.array([i for i in all_index if i != data_idx])
    index_list = []    
    
    # Indicate how to sample the rest of the dataset.
    if configs['split_method'] == 'uniform':
        # Placeholder for the existing models
        for _ in range(num_total-num_models):
            index_list.append({})
        for _ in range(num_models):
            if data_type == 'include':
                train_index = np.random.choice(all_index_exclude_z,int((configs['f_train'])*N)-1,replace=False)
                index_list.append({'train':np.append(train_index,data_idx),'test':all_index,'audit':all_index})
            elif data_type == 'exclude':
                train_index = np.random.choice(all_index_exclude_z,int((configs['f_train'])*N),replace=False)
                index_list.append({'train':train_index,'test':all_index,'audit':all_index})
            else:
                raise ValueError(f"{data_type} is not supported. Please use the value include or exclude to indicate whether you want to generate a set of dataset with or without the target point.")
    
    # We generate a list of dataset which is the same as the training dataset of the models indicated by the matched_in_idx but excluding the target point.
    elif configs['split_method'] == 'leave_one_out':
        assert matched_in_idx is not None, "Please indicate the in-world model metdadata"
        assert len(matched_in_idx) >= num_models, "Input enough in-world (with the target point z) to generate the out world"
        
        index_list = [] # List of data split
        all_index = np.arange(N)
        all_index_exclude_z = np.array([i for i in all_index if i != data_idx])
        
        for _ in range(num_total - num_models):
            index_list.append({})
            associated_models.append(None)
            
        for metadata_idx in matched_in_idx:
            metadata = model_metadata_list['model_metadata'][metadata_idx]
            train_index = np.delete(metadata['train_split'],np.where(metadata['train_split']==data_idx)[0])
            index_list.append({'train':train_index,'test':[i for i in all_index if i not in train_index and i != data_idx],'audit':[i for i in all_index if i not in train_index and i != data_idx]}) #Note: Since we are intested in the individual privacy risk, we consider the whole dataset as the test and audit dataset
            associated_models.append(metadata['idx'])
    
    else:
        raise ValueError(f"{configs['split_method']} is not supported. Please use uniform or leave_one_out splitting method.")
    
    dataset_splits = {'split':index_list,'split_method':configs['split_method'],'associated_models':associated_models}
    return dataset_splits





def prepare_models(dataset,data_split,configs,model_metadata_list,matched_idx=None): 
    """Train models based on the dataset list

    Args:
        dataset: The whole dataset 
        data_split (dict): Data split information. 'split' contains a list of dict, each of which has the train, test and audit information. 'split_method' indicates the how the dataset is generated.
        configs (dict): Indicate the traininig information
        model_metadata_list (dict): Metadata information about the existing models.
        matched_idx (List, optional): Index list of existing models that matchs configuration. Defaults to None.

    Returns:
        model_list: List of trained models
        model_metadata_list: Updated Metadata of the existing models
        matched_idx: Updated index list that matches the target model configurations.
    """
    # Initialize the model list
    model_list = [] 
    
    # Load the existing target models.
    if matched_idx is not None: 
        for metadata_idx in matched_idx:
            metadata = model_metadata_list['model_metadata'][metadata_idx]    
            model = get_model(configs['model_name'])
            with open(f"{metadata['model_path']}",'rb') as f:
                model_weight = pickle.load(f)
            model.load_state_dict(model_weight)
            model_list.append(model)
        print(f"Load existing {len(model_list)} target models")
    

    # Train the additional target models based on the dataset split
    for split in range(len(model_list),len(data_split['split'])):
        meta_data = {}        
        baseline_time = time.time()
        
        train_data = get_cifar10_subset(dataset,data_split['split'][split]['train'])
        test_data = get_cifar10_subset(dataset,data_split['split'][split]['test'])
        train_loader = torch.utils.data.DataLoader(train_data, batch_size=configs['batch_size'],shuffle=True, num_workers=2)
        test_loader = torch.utils.data.DataLoader(test_data, batch_size=configs['test_batch_size'],shuffle=False, num_workers=2)
        
        print(50*"-")
        print(f'Training the {split}-th model: the training dataset of size {len(train_data)} and test dataset of size {len(test_data)}')
        
        # Train the target model based on the configurations.
        model = get_model(configs['model_name'])    
        model = train(model,train_loader,configs,test_loader)
        model_list.append(copy.deepcopy(model))
        
        logging.info(f'Prepare {split}-th  target model costs {time.time()-baseline_time} seconds')
        print(50*"-")
        

        # Update the model metadata and save the model
        model_idx = model_metadata_list['current_idx']
        model_metadata_list['current_idx'] +=1
        with open(f'{log_dir}/model_{model_idx}.pkl','wb') as f:
            pickle.dump(model.state_dict(),f)
        meta_data['train_split'] = data_split['split'][split]['train']
        meta_data['test_split'] = data_split['split'][split]['test']
        meta_data['audit_split'] = data_split['split'][split]['audit']
        meta_data['optimizer'] = configs['optimizer']
        meta_data['batch_size'] = configs['batch_size']
        meta_data['epochs'] = configs['epochs']
        meta_data['model_name'] = configs['model_name']
        meta_data['split_method'] = data_split['split_method']
        meta_data['idx'] = model_idx
        meta_data['lr'] = configs['lr']
        meta_data['wd'] = configs['wd']
        meta_data['model_path'] = f'{log_dir}/model_{model_idx}.pkl'
        # Check if there is any associated model (trained on the dataset differ by one record)
        if 'associated_models' in data_split:
            if split < len(data_split['associated_models']):
                    meta_data['associated_models'] = {'remove_from':data_split['associated_models'][split]}
        model_metadata_list['model_metadata'][model_idx] = meta_data
        with open(f'{log_dir}/models_metadata.pkl','wb') as f:
            pickle.dump(model_metadata_list,f)
        
        
        # Update the matched_idx 
        if matched_idx is not None:
            matched_idx.append(model_idx)
            
            
    return model_list,model_metadata_list, matched_idx



def get_info_source_population_attack(dataset,data_split,model, configs):
    """Prepare the information source for calling the core of privacy meter for the population attack

    Args:
        dataset: The whole dataset 
        data_split (dict): Data split information. 'split' contains a list of dict, each of which has the train, test and audit information. 'split_method' indicates the how the dataset is generated.
        model (model): Target Model.
        configs (dict): Auditing configuration

    Returns:
        target_dataset: List of target dataset on which we want to infer the membership
        audit_dataset:  List of auditing datasets we use for launch the attack
        target_model: List of target models we want to audit 
        reference_model: List of reference models (which is the target model based on population attack)
    """
    train_data = get_cifar10_subset(dataset,data_split['train'],is_tensor=True)
    test_data = get_cifar10_subset(dataset, data_split['test'],is_tensor=True)
    audit_data = get_cifar10_subset(dataset, data_split['audit'],is_tensor=True)
    target_dataset = Dataset(
        data_dict={
            'train': {'x': train_data.data, 'y': train_data.targets},
            'test': {'x': test_data.data, 'y': test_data.targets},
        },
        default_input='x',
        default_output='y'
    )
    
    audit_dataset  = Dataset(
        data_dict={
            'train': {'x': audit_data.data, 'y': audit_data.targets}
        },
        default_input='x',
        default_output='y'
    )
    target_model = PytorchModelTensor(model_obj=model, loss_fn=nn.CrossEntropyLoss(),device=configs['device'], batch_size=configs['audit_batch_size'])
    return [target_dataset], [audit_dataset], [target_model], [target_model]


def get_info_source_reference_attack(dataset,data_split,model,configs,model_metadata_list,matched_reference_idx=None):
    """Prepare the information source for the reference attacks

     Args:
        dataset: The whole dataset 
        data_split (dict): Data split information. 'split' contains a list of dict, each of which has the train, test and audit information. 'split_method' indicates the how the dataset is generated.
        model (model): Target Model.
        configs (dict): Auditing configuration.
        model_metadata_list (dict): Model metedata dict.
        matched_reference_idx (list, optional): List of existing reference models. Defaults to None.
    
    Returns:
        target_dataset: List of target dataset on which we want to infer the membership.
        audit_dataset:  List of auditing datasets we use for launch the attack (which is the target dataset based on reference attack)
        target_model: List of target models we want to audit.
        reference_model: List of reference models.
        model_metadata_list: Updated metadata for the trained model.
        
    """
   
    # Construct the target dataset and target models
    train_data = get_cifar10_subset(dataset,data_split['train'],is_tensor=True)
    test_data = get_cifar10_subset(dataset, data_split['test'],is_tensor=True)
    target_dataset = Dataset(
        data_dict={
            'train': {'x': train_data.data, 'y': train_data.targets},
            'test': {'x': test_data.data, 'y': test_data.targets},
        },
        default_input='x',
        default_output='y'
    )
    target_model = PytorchModelTensor(model_obj=model, loss_fn=nn.CrossEntropyLoss(),device=configs['device'], batch_size=configs['audit_batch_size'])
    
    # Construct reference models
    reference_models = []
    # Load existing reference models from disk
    if matched_reference_idx is not None: 
        for metadata_idx in matched_reference_idx:
            metadata = model_metadata_list['model_metadata'][metadata_idx]
            model = get_model(configs['model_name'])
            with open(f"{metadata['model_path']}",'rb') as f:
                model_weight = pickle.load(f)
            model.load_state_dict(model_weight)
            reference_models.append(PytorchModelTensor(model_obj=model, loss_fn=nn.CrossEntropyLoss(),device=configs['device'], batch_size=configs['audit_batch_size']))
    
    print(f"Load existing {len(reference_models)} reference models")
    
    # Train additional reference models
    num_reference_models = configs['num_reference_models'] - len(reference_models)    
    for reference_idx in range(num_reference_models):
        reference_data_idx =  get_split(data_split['audit'],None,size=int(configs['f_reference_dataset']*len(train_data)),split_method=configs['split_method'])
        
        print(f'Training  {reference_idx}-th reference model')
        start_time = time.time()
        
        reference_loader = torch.utils.data.DataLoader(get_cifar10_subset(dataset,reference_data_idx), batch_size=configs['batch_size'],shuffle=True, num_workers=2) 
        reference_model = get_model(configs['model_name'])
        reference_model = train(reference_model,reference_loader,configs,None)
    
        logging.info(f'Prepare {reference_idx}-th reference model costs {time.time()-start_time} seconds')
        
        
        model_idx = model_metadata_list['current_idx']
        model_metadata_list['current_idx'] +=1
        with open(f'{log_dir}/model_{model_idx}.pkl','wb') as f:
            pickle.dump(model.state_dict(),f)
        
        meta_data = {}
        meta_data['train_split'] = reference_data_idx
        meta_data['optimizer'] = configs['optimizer']
        meta_data['batch_size'] = configs['batch_size']
        meta_data['epochs'] = configs['epochs']
        meta_data['split_method']  = configs['split_method']
        meta_data['idx'] = model_idx
        meta_data['lr'] = configs['lr']
        meta_data['wd'] = configs['wd']
        meta_data['model_name'] = configs['model_name']
        meta_data['model_path'] = f'{log_dir}/model_{model_idx}.pkl' 
        model_metadata_list['model_metadata'][model_idx] = meta_data
        reference_models.append(PytorchModelTensor(model_obj=reference_model, loss_fn=nn.CrossEntropyLoss(),device=configs['device'], batch_size=configs['audit_batch_size']))
        
    return [target_dataset], [target_dataset], [target_model], reference_models,model_metadata_list




def prepare_information_source(dataset,data_split,model_list,configs,model_metadata_list,matched_reference_idx=None):
    """Prepare the information source for calling the core of the privacy meter
    Args:
        dataset: The whole dataset 
        data_split (dict): Data split information. 'split' contains a list of dict, each of which has the train, test and audit information. 'split_method' indicates the how the dataset is generated.
        model_list (List): List of target models.
        configs (dict): Auditing configuration.
        model_metadata_list (dict): Model metedata dict.
        matched_reference_idx (list, optional): List of existing reference models. Defaults to None.
        
    Returns:
        target_info_source_list (List):
        reference_info_source_list (List):
        metric_list (List):
        log_dir_list (List): 
        model_metadata_list (dict): Updated metadata for the trained model. 
    """
    reference_info_source_list = []
    target_info_source_list = []
    metric_list = []
    log_dir_list = []
    
    # Prepare the information source for each target model
    for split in range(len(model_list)): 
        print(f'preparing information sources for {split}-th target model')
        if configs['algorithm'] == 'population':
            target_dataset, audit_dataset, target_model, audit_models = get_info_source_population_attack(dataset,data_split['split'][split],model_list[split],configs)
            metrics = MetricEnum.POPULATION
        elif configs['algorithm'] == 'reference':
            # Check if there are existing reference models
            if matched_reference_idx != None and len(matched_reference_idx) > split:
                target_dataset, audit_dataset, target_model, audit_models, model_metadata_list = get_info_source_reference_attack(dataset,data_split['split'][split],model_list[split],configs,model_metadata_list,matched_reference_idx[split])
            else:
                target_dataset, audit_dataset, target_model, audit_models, model_metadata_list = get_info_source_reference_attack(dataset,data_split['split'][split],model_list[split],configs,model_metadata_list)
            metrics = MetricEnum.REFERENCE      
        metric_list.append(metrics)  
        
        target_info_source = InformationSource(models=target_model, datasets=target_dataset)
        reference_info_source = InformationSource(models=audit_models,datasets=audit_dataset)
        reference_info_source_list.append(reference_info_source)
        target_info_source_list.append(target_info_source)
        
        # Save the log_dir for attacking different target model
        log_dir_path = f"{log_dir}/{configs['report_log']}/signal_{split}"
        Path(log_dir_path).mkdir(parents=True, exist_ok=True)
        log_dir_list.append(log_dir_path)
    
    
    return target_info_source_list, reference_info_source_list,metric_list,log_dir_list, model_metadata_list



def prepare_priavcy_risk_report(audit_results,configs,save_path=None):
    """Generate privacy risk report based on the auditing report

    Args:
        audit_results: Privacy meter results.
        configs (dict): Auditing configuration.
        save_path (str, optional): Report path. Defaults to None.

    Raises:
        NotImplementedError: Check if the report for the privacy game is implemented.

    """
    audit_report.REPORT_FILES_DIR = 'privacy_meter/report_files'
    if save_path is None:
        save_path = log_dir
    
    # Generate privacy risk report for auditing the model
    if len(audit_results) == 1 and configs['privacy_game']=='privacy_loss_model':
        ROCCurveReport.generate_report(
            metric_result=audit_results[0],
            inference_game_type=InferenceGame.PRIVACY_LOSS_MODEL,
            save=True, 
            filename = f"{save_path}/ROC.png"
        )
        SignalHistogramReport.generate_report(
            metric_result=audit_results[0][0],
            inference_game_type=InferenceGame.PRIVACY_LOSS_MODEL,
            save=True, 
            filename = f"{save_path}/Histogram.png"
        )
    # Generate privacy risk report for auditing the training algorithm
    elif len(audit_results) > 1 and configs['privacy_game'] == 'avg_privacy_loss_training_algo':
        ROCCurveReport.generate_report(
            metric_result=audit_results,
            inference_game_type=InferenceGame.AVG_PRIVACY_LOSS_TRAINING_ALGO,
            save=True, 
            filename = f"{save_path}/ROC.png"
        )
        
        SignalHistogramReport.generate_report(
            metric_result=audit_results,
            inference_game_type=InferenceGame.AVG_PRIVACY_LOSS_TRAINING_ALGO,
            save=True, 
            filename = f"{save_path}/Histogram.png"
        )

    else:
        raise NotImplementedError(f"{configs['privacy_game']} is not implemented yet")
        


def load_existing_target_model(N, model_metadata_list,configs):
    """Check if there are trained models that matches the training configuration.

    Args:
        N (int): Size of the whole training dataset. 
        model_metadata_list (dict): Model metedata dict.
        configs (dict): Training target models configuration.

    Raises:
        ValueError: Check if the key for the configuration takes value from data_idx and model_idx

    Returns:
        matched_idx: List of target model index which matches the conditions
    """
    matched_idx = []
    # Specify the conditions. 
    matching_key = ['optimizer','batch_size','epochs','lr','wd']
    for meta_idx in model_metadata_list['model_metadata']:
        meta_data = model_metadata_list['model_metadata'][meta_idx]
        # Check if conditions are satisfied.
        if len(meta_data['train_split']) == int(N*configs['data']['f_train']) and np.mean([meta_data[key] == configs['train'][key] for key in matching_key]) ==1:
            # Check if we need to load the model based on model_idx or load a set of model related to a point indicated by data_idx. 
            if configs['train']['key'] == 'none':
                matched_idx.append(meta_idx)
            elif configs['train']['key'] == 'data_idx':
                # Check if the data is included or excluded from the training dataset of the target model
                if (configs['train']['type'] == 'include' and configs['train']['idx'] in meta_data['train_split']) or (configs['train']['type'] == 'exclude' and configs['train']['idx'] not in meta_data['train_split']): #check if the idx is in the training dataset size.
                    matched_idx.append(meta_idx)
            elif configs['train']['key'] == 'model_idx':
                # Check if the model id equals to what is specified.
                if meta_data['idx'] == configs['train']['idx']:
                    matched_idx.append(meta_idx)
                    return matched_idx
            else:
                raise ValueError(f'Key can only be model_idx or data_idx')
        
        # Check if we have load enough train models
        if len(matched_idx) == configs['train']['num_target_model']:
            break
    return matched_idx
   


def load_existing_reference_models(N, model_metadata_list,configs,matched_target_idx):
    """Check if there are trained models that matches the training configuration.

    Args:
        N (int): Size of the whole training dataset. 
        model_metadata_list (dict): Model metedata dict.
        configs (dict): Training target models configuration.
        matched_target_idx (List): List of existing target model index.

    Raises:
        ValueError: Check if the key for the configuration takes value from data_idx and model_idx

    Returns:
        reference_matched_idx_list: List of reference model index which matches the conditions
    """
    
    
    
    reference_matched_idx_list = []
    matching_key = ['optimizer','batch_size','epochs','lr','wd']
    
    for target_idx in matched_target_idx:
        reference_matched_idx = []
        for meta_idx in model_metadata_list['model_metadata']:
            meta_data = model_metadata_list['model_metadata'][meta_idx]
            # Check if the training configurations are satisfied.
            if meta_idx != target_idx and len(meta_data['train_split']) == int(N*configs['data']['f_audit']) and np.mean([meta_data[key] == configs['audit'][key] for key in matching_key]) == 1:
                if configs['audit']['key'] == 'none':
                    # Check if the reference models' training dataset satisfy the conditions
                    if configs['data']['split_method'] == 'no_overlapping':
                        if len(set(meta_data['train_split']) - set(model_metadata_list['model_metadata'][target_idx]['train_split'])) == len(set(meta_data['train_split'])):
                            reference_matched_idx.append(meta_idx)    
                    elif configs['data']['split_method'] == 'uniform':
                        if collections.Counter(meta_data['train_split']) != collections.Counter(model_metadata_list['model_metadata'][target_idx]['train_split']):
                            reference_matched_idx.append(meta_idx)    
                            
                elif configs['audit']['key'] == 'data_idx':
                    # Check if the reference models are trained with or without the points indicated by idx
                    if (configs['audit']['type'] == 'include' and configs['audit']['idx'] in meta_data['train_split']) or (configs['audit']['type'] == 'exclude' and configs['audit']['idx'] not in meta_data['train_split']): #check if the idx is in the training dataset size.
                        reference_matched_idx.append(meta_idx)
                else:
                    raise ValueError(f'key can only be data idx for loading reference models')
            # Check if the existing referencce models already satisfy the constraints            
            if len(reference_matched_idx) == configs['audit']['num_reference_models']:
                break
        reference_matched_idx_list.append(reference_matched_idx)
                        
    return reference_matched_idx_list



if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('--cf', type=str, default="config_model.yaml",help='Yaml file which contains the configurations')
    
    # Load the parameters 
    args = parser.parse_args()
    config_file = open(args.cf, 'r')
    configs = yaml.load(config_file,Loader=yaml.Loader) 
    
    # Set the random seed, log_dir and inference_game
    torch.manual_seed(configs['run']['random_seed']) 
    global log_dir
    log_dir = configs['run']['log_dir']
    inference_game_type = configs['audit']['privacy_game'].upper()
    
    # Create folders for saving the logs if they do not exist
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    Path(f"{log_dir}/{configs['audit']['report_log']}").mkdir(parents=True, exist_ok=True)
    
    
    
    start_time = time.time()


    # Load or initialize models based on metadata
    if os.path.exists((f'{log_dir}/models_metadata.pkl')):
        with open(f'{log_dir}/models_metadata.pkl','rb') as f:
            model_metadata_list = pickle.load(f)
    else: 
        model_metadata_list = {'model_metadata':{},'current_idx':0}

    
    # Load the dataset
    baseline_time = time.time()
    dataset = get_dataset(configs['data']['dataset'],log_dir) 
    
    
    # Check the auditing game. If we are interested in auditing the privacy risk for a model or a training algorithm (set of models trained using the same algorithm).
    if configs['audit']['privacy_game'] in ['avg_privacy_loss_training_algo','privacy_loss_model']:
        if configs['audit']['privacy_game'] == 'privacy_loss_model':
            assert configs['train']['num_target_model'] == 1, "only need one model for auditing the privacy risk for a trained model"
        elif configs['audit']['privacy_game'] == 'avg_privacy_loss_training_algo':
            assert configs['train']['num_target_model'] > 1, "need more models for computing the average privacy loss for an algorithm"
        else:
            raise ValueError(f"{configs['audit']['privacy_game']} has not been implemented")
        
        # Load the saved models which matches the requirments for reference models and target models
        if model_metadata_list['current_idx'] > 0:
            matched_idx = load_existing_target_model(len(dataset),model_metadata_list,configs)
            if configs['audit']['algorithm'] == 'reference' and matched_idx is not None:
                matched_reference_idx = load_existing_reference_models(len(dataset),model_metadata_list,configs,matched_idx)
            else:
                matched_reference_idx = None
        else:
            matched_idx,matched_reference_idx = None,None
            
        # Prepare the datasets
        print(25*"#"+"Prepare the the daatsets"+25*"#")
        data_split_info = prepare_datasets(len(dataset),configs['train']['num_target_model'],configs['data'],model_metadata_list,matched_idx)    
        logging.info(f'Prepare the datasets costs {time.time()-baseline_time} seconds')
       
        # Prepare the target models
        print(25*"#"+"Prepare the the target models"+25*"#")
        baseline_time = time.time()
        model_list, model_metadata_list,matched_idx = prepare_models(dataset,data_split_info,configs['train'],model_metadata_list,matched_idx)
        logging.info(f'Prepare the target model costs {time.time()-baseline_time} seconds')
        
        # Save the updated metadata
        with open(f'{log_dir}/models_metadata.pkl','wb') as f:
            pickle.dump(model_metadata_list,f)
            
        # Prepare the information sources
        print(25*"#"+"Prepare the information source, including attack models"+25*"#")
        baseline_time = time.time()
        target_info_source, reference_info_source,metrics,log_dir_list,model_metadata_list = prepare_information_source(dataset,data_split_info,model_list,configs['audit'],model_metadata_list,matched_reference_idx)
        logging.info(f'Prepare the information source costs {time.time()-baseline_time} seconds')
        
        # Call core of privacy meter 
        print(25*"#"+"Auditing the privacy risk"+25*"#")
        baseline_time = time.time()
        audit_obj = Audit(
            metrics=metrics,
            inference_game_type=inference_game_type,
            target_info_sources=target_info_source,
            reference_info_sources=reference_info_source,
            fpr_tolerances=None,
            logs_directory_names=log_dir_list 
        )
        audit_obj.prepare()
        audit_results = audit_obj.run()  
        logging.info(f'Prepare privacy meter results costs {time.time()-baseline_time} seconds')
        
        # Generate the privacy risk report
        print(25*"#"+"Generating privacy risk report"+25*"#")
        baseline_time = time.time()
        prepare_priavcy_risk_report(audit_results,configs['audit'],save_path=f"{log_dir}/{configs['audit']['report_log']}")
        print(100*"#")
        
        
        logging.info(f'Prepare the plot for the privacy risk report costs {time.time()-baseline_time} seconds')    
        logging.info(f'Run the priavcy meter for the all steps costs {time.time()-start_time} seconds')
        

    # Auditing the priavcy risk for an individual data point
    elif configs['audit']['privacy_game'] == 'privacy_loss_sample':
        # Construct the models trained on data indicated by train.idx and not trained on it
        in_configs = copy.deepcopy(configs)
        in_configs['train']['type'] = 'include'
        in_configs['train']['num_target_model'] = configs['train']['num_in_models']
        
        out_configs = copy.deepcopy(configs)
        out_configs['train']['type'] = 'exclude'
        out_configs['train']['num_target_model']  = configs['train']['num_out_models']
        
        # Load existing models that match the requirement
        matched_in_idx = load_existing_target_model(len(dataset),model_metadata_list,in_configs)
        matched_out_idx = load_existing_target_model(len(dataset),model_metadata_list,out_configs)
        
        # Train additional models if the existing models are not enough
        if len(matched_in_idx) <  configs['train']['num_in_models']:
            data_split_info_in = prepare_datasets_for_sample_privacy_risk(len(dataset), configs['train']['num_in_models'],configs['train']['num_in_models']- len(matched_in_idx),configs['train']['idx'],configs['data'],'in',model_metadata_list)
            in_model_list, model_metadata_list,matched_in_idx = prepare_models(dataset,data_split_info_in,configs['train'],model_metadata_list,matched_in_idx)
        else:
            in_model_list, model_metadata_list,matched_in_idx = prepare_models(dataset,{'split':[]},configs['train'],model_metadata_list,matched_in_idx[:configs['train']['num_in_models']])
            
            
        if len(matched_out_idx) <  configs['train']['num_out_models']:
            data_split_info_out = prepare_datasets_for_sample_privacy_risk(len(dataset),configs['train']['num_out_models'], configs['train']['num_out_models']- len(matched_out_idx),configs['train']['idx'],configs['data'],'out',model_metadata_list)
            out_model_list, model_metadata_list,matched_out_idx = prepare_models(dataset,data_split_info_out,configs['train'],model_metadata_list,matched_out_idx)
        else:
            out_model_list, model_metadata_list,matched_out_idx = prepare_models(dataset,{'split':[]},configs['train'],model_metadata_list,matched_out_idx[:configs['train']['num_out_models']])


        # Obtain models trained on train.idx and without it
        in_model_list_pm = [PytorchModelTensor(model_obj=model, loss_fn=nn.CrossEntropyLoss(), batch_size=1000) for model in in_model_list]
        out_model_list_pm = [PytorchModelTensor(model_obj=model, loss_fn=nn.CrossEntropyLoss(), batch_size=1000) for model in out_model_list]

                

        # Test the models' performance on the data indicated by the audit.idx
        target_data = get_cifar10_subset(dataset,[configs['audit']['idx']],is_tensor=True)
        in_signal = np.array([model.get_loss(target_data.data,target_data.targets).item() for model in in_model_list_pm])
        out_signal = np.array([model.get_loss(target_data.data,target_data.targets).item() for model in out_model_list_pm])
        
        # Rescale the loss
        in_signal = in_signal+0.000001 # avoid nan
        in_signal = np.log(np.divide(np.exp(- in_signal), (1 - np.exp(- in_signal))))
        out_signal = out_signal+0.000001 # avoid nan
        out_signal = np.log(np.divide(np.exp(- out_signal), (1 - np.exp(- out_signal))))
        
        # Generate the privacy risk report
        labels = np.concatenate([np.ones(in_signal.shape[0]),np.zeros(out_signal.shape[0])])
        histogram = sns.histplot(
            data=pd.DataFrame({
                'Signal': np.concatenate([in_signal,out_signal]),
                'Membership': [f"In ({configs['train']['idx']})" if y == 1 else f"Out ({configs['train']['idx']})"  for y in labels]
            }),
            x='Signal',
            hue='Membership',
            element='step',
            kde=True
        )
        plt.grid()
        plt.xlabel(f"Signal value")
        plt.ylabel('Number of Models')
        plt.title(f"Signal histogram for data point {configs['audit']['idx']}")
        plt.savefig(f"{log_dir}/individual_pr_{configs['train']['idx']}_{configs['audit']['idx']}.png")
        
        
        
