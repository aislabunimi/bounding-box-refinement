import random
import time

from torch.optim import lr_scheduler
from tqdm import tqdm

from doors_detection_long_term.doors_detector.dataset.torch_dataset import DEEP_DOORS_2_LABELLED, FINAL_DOORS_DATASET
import numpy as np
import torch.optim
from torch.utils.data import DataLoader
from doors_detection_long_term.doors_detector.dataset.dataset_doors_final.datasets_creator_doors_final import DatasetsCreatorDoorsFinal
from doors_detection_long_term.doors_detector.models.model_names import YOLOv5, FASTER_RCNN
from doors_detection_long_term.doors_detector.models.faster_rcnn import *
from doors_detection_long_term.doors_detector.models.yolov5_repo.utils.general import check_amp
from doors_detection_long_term.doors_detector.models.yolov5_repo.utils.loss import ComputeLoss
from doors_detection_long_term.doors_detector.models.yolov5_repo.utils.torch_utils import smart_optimizer
from doors_detection_long_term.doors_detector.utilities.plot import plot_losses
from doors_detection_long_term.doors_detector.utilities.collate_fn_functions import collate_fn_faster_rcnn
from doors_detection_long_term.scripts.doors_detector.dataset_configurator import *
from doors_detection_long_term.doors_detector.utilities.collate_fn_functions import seed_everything


device = 'cuda'

houses = ['floor1', 'floor4', 'chemistry_floor0', 'house_matteo']
epochs_general_detector = [40, 60]
epochs_qualified_detectors = [20, 40]
fine_tune_quantity = [15, 25, 50, 75]


# Params
params = {
    #'epochs': 40,
    'batch_size': 4,
    'seed': 0
}

def prepare_model(description, reload_model, restart_checkpoint):
    model = FasterRCNN(model_name=FASTER_RCNN, n_labels=len(labels.keys()) + 1, pretrained=reload_model, dataset_name=FINAL_DOORS_DATASET, description=description)

    logs = {'train': [], 'train_after_backpropagation': [], 'validation': [], 'test': [], 'time': []}
    optimizer_state_dict = {}
    lr_scheduler_state_dict = {}
    start_epoch = 0
    if restart_checkpoint:
        checkpoint = model.load_checkpoint()
        start_epoch = checkpoint['epoch'] + 1
        logs = checkpoint['logs']
        optimizer_state_dict = checkpoint['optimizer_state_dict']
        lr_scheduler_state_dict = checkpoint['lr_scheduler_state_dict']


    params = [(n, p) for n, p in model.named_parameters() if p.requires_grad]

    optimizer = torch.optim.SGD([p for n, p in params], lr=0.001,
                                momentum=0.9, nesterov=True)

    if restart_checkpoint:
        optimizer.load_state_dict(optimizer_state_dict)

    optimizer.zero_grad()

    scheduler = torch.optim.lr_scheduler.StepLR(optimizer,
                                                step_size=3,
                                                gamma=0.1)
    if restart_checkpoint:
        scheduler.load_state_dict(lr_scheduler_state_dict)

    return model, optimizer, scheduler, logs

if __name__ == '__main__':

    # Fix seeds
    seed_everything(params['seed'])

    # Train the general detector with gibson and deep_door_2
    for name, (train, validation, labels, _) in [('gibson', get_final_doors_dataset_all_envs()), ('deep_doors_2', get_deep_doors_2_relabelled_dataset_for_gd()), ('gibson_deep_doors_2', get_gibson_and_deep_door_2_dataset(half=False))]:
        epoch_count = 0
        print(f'Train set size: {len(train)}', f'Validation set size: {len(validation)}')
        data_loader_train = DataLoader(train, batch_size=params['batch_size'], collate_fn=collate_fn_faster_rcnn, shuffle=False, num_workers=4)
        data_loader_validation = DataLoader(validation, batch_size=params['batch_size'], collate_fn=collate_fn_faster_rcnn, drop_last=False, num_workers=4)

        model, optimizer, scheduler, logs = prepare_model(globals()[f'EXP_GENERAL_DETECTOR_{name}_{epochs_general_detector[epoch_count]}_EPOCHS'.upper()], reload_model=False, restart_checkpoint=False)
        print_logs_every = 10
        model.to('cuda')

        start_time = time.time()

        for epoch in range(epochs_general_detector[-1]):

            temp_logs = {'train': [], 'train_after_backpropagation': [], 'validation': []}
            accumulate_loss = []

            model.train()
            optimizer.zero_grad()
            for d, data in tqdm(enumerate(data_loader_train), total=len(data_loader_train), desc=f'Epoch {epoch} - Train GD with {name.upper()}'):
                images, targets, new_targets = data
                images = list(image.to(device) for image in images)
                new_targets = [{k: v.to(device).to(torch.int64) for k, v in t.items()} for t in new_targets]


                with torch.cuda.amp.autocast(enabled=False):
                    loss_dict = model(images, new_targets)
                    losses = sum(loss for loss in loss_dict.values())
                    #print('losses', loss_dict)

                loss_value = losses.item()
                #print('loss_value', loss_value)

                optimizer.zero_grad()
                losses.backward()
                optimizer.step()


                accumulate_loss.append(loss_value)

            #scheduler.step()
            logs['time'].append(time.time() - start_time)
            epoch_total = {}
            for d in temp_logs['train']:
                for k in d:
                    epoch_total[k] = epoch_total.get(k, 0) + d[k]

            logs['train'].append({'loss': sum(accumulate_loss, 0) / len(accumulate_loss)})

            #print(f'----> {house} - EPOCH SUMMARY TRAIN [{epoch}] -> ' + ', '.join([f'{k}: {v}' for k, v in logs['train'][epoch].items()]))

            # Train loss after backpropagation
            with torch.no_grad():
                accumulate_loss = []
                for i, data in tqdm(enumerate(data_loader_train), total=len(data_loader_train), desc=f'Epoch {epoch} - Test model with training data of {name.upper()} dataset'):
                    images, targets, new_targets = data
                    images = list(image.to(device) for image in images)
                    new_targets = [{k: v.to(device).to(torch.int64) for k, v in t.items()} for t in new_targets]


                    with torch.cuda.amp.autocast(enabled=False):
                        loss_dict = model(images, new_targets)
                        losses = sum(loss for loss in loss_dict.values())

                    loss_value = losses.item()
                    accumulate_loss.append(loss_value)

            logs['train_after_backpropagation'].append({'loss': sum(accumulate_loss, 0) / len(accumulate_loss)})

            #print(f'----> EPOCH SUMMARY TRAIN AFTER BACKPROP [{epoch}] -> [{i}/{len(data_loader_train)}]: ' + ', '.join([f'{k}: {v}' for k, v in logs['train_after_backpropagation'][epoch].items()]))

            # Validation
            with torch.no_grad():
                accumulate_loss = []
                for i, data in tqdm(enumerate(data_loader_validation), total=len(data_loader_validation), desc=f'Epoch {epoch} - Test model with validation data of {name.upper()} dataset'):
                    images, targets, new_targets = data
                    images = list(image.to(device) for image in images)
                    new_targets = [{k: v.to(device).to(torch.int64) for k, v in t.items()} for t in new_targets]

                    with torch.cuda.amp.autocast(enabled=False):
                        loss_dict = model(images, new_targets)
                        losses = sum(loss for loss in loss_dict.values())

                    loss_value = losses.item()
                    accumulate_loss.append(loss_value)

            logs['validation'].append({'loss': sum(accumulate_loss, 0) / len(accumulate_loss)})
            logs['test'].append({'loss': 0.})
            print(f'----> EPOCH {epoch} SUMMARY: ' + ', '.join([f'{k}: {v[epoch]}' for k, v in logs.items()]))

            plot_losses(logs)

            model.save(epoch=epoch,
                   optimizer_state_dict=optimizer.state_dict(),
                   lr_scheduler_state_dict=scheduler.state_dict(),
                   params=params,
                   logs=logs,
                   )

            # Change the model description on each epoch step
            if epoch == epochs_general_detector[epoch_count] - 1 and epoch_count < len(epochs_general_detector) -1:
                epoch_count += 1
                model.set_description(globals()[f'EXP_GENERAL_DETECTOR_{name}_{epochs_general_detector[epoch_count]}_EPOCHS'.upper()])


    # Qualify the general detectors trained before
    for house, gd_dataset, epochs_general, quantity in [(h, eg, e, q) for h in houses for eg in ['gibson', 'deep_doors_2', 'gibson_deep_doors_2'] for e in epochs_general_detector for q in fine_tune_quantity]:
        epoch_count = 0
        print(f'{house}, general detectors trained with {gd_dataset} for {epochs_general} epochs, fine tune train set: {quantity}')
        train, test, labels, _ = get_final_doors_dataset_real_data(folder_name=house, train_size=quantity / 100)
        print(f'Train set size: {len(train)}', f'Test set size: {len(test)}')
        data_loader_train = DataLoader(train, batch_size=params['batch_size'], collate_fn=collate_fn_faster_rcnn, shuffle=False, num_workers=4)
        data_loader_test = DataLoader(test, batch_size=params['batch_size'], collate_fn=collate_fn_faster_rcnn, drop_last=False, num_workers=4)

        model, optimizer, scheduler, logs = prepare_model(globals()[f'EXP_GENERAL_DETECTOR_{gd_dataset}_{epochs_general}_EPOCHS'.upper()], reload_model=False, restart_checkpoint=False)
        print_logs_every = 10
        model.to('cuda')
        model.set_description(globals()[f'EXP_2_{house}_{gd_dataset}_EPOCHS_GD_{epochs_general}_EPOCHS_QD_{epochs_qualified_detectors[epoch_count]}_FINE_TUNE_{quantity}'.upper()])
        start_time = time.time()

        for epoch in range(epochs_qualified_detectors[-1]):

            temp_logs = {'train': [], 'train_after_backpropagation': [], 'test': []}
            accumulate_loss = []

            model.train()
            optimizer.zero_grad()
            for d, data in tqdm(enumerate(data_loader_train), total=len(data_loader_train), desc=f'{house} - Epoch {epoch} - Starting GD trained with {gd_dataset} - Fine tune with {quantity}% of examples of {house}'):
                images, targets, new_targets = data
                images = list(image.to(device) for image in images)
                new_targets = [{k: v.to(device).to(torch.int64) for k, v in t.items()} for t in new_targets]


                with torch.cuda.amp.autocast(enabled=False):
                    loss_dict = model(images, new_targets)
                    losses = sum(loss for loss in loss_dict.values())
                    #print('losses', loss_dict)

                loss_value = losses.item()
                #print('loss_value', loss_value)

                optimizer.zero_grad()
                losses.backward()
                optimizer.step()
                accumulate_loss.append(loss_value)

            #scheduler.step()
            logs['time'].append(time.time() - start_time)
            epoch_total = {}
            for d in temp_logs['train']:
                for k in d:
                    epoch_total[k] = epoch_total.get(k, 0) + d[k]

            logs['train'].append({'loss': sum(accumulate_loss, 0) / len(accumulate_loss)})

            # Train loss after backpropagation
            with torch.no_grad():
                accumulate_loss = []
                for i, data in tqdm(enumerate(data_loader_train), total=len(data_loader_train), desc=f'{house} - Epoch {epoch} - Test model with training data'):
                    images, targets, new_targets = data
                    images = list(image.to(device) for image in images)
                    new_targets = [{k: v.to(device).to(torch.int64) for k, v in t.items()} for t in new_targets]

                    with torch.cuda.amp.autocast(enabled=False):
                        loss_dict = model(images, new_targets)
                        losses = sum(loss for loss in loss_dict.values())

                    loss_value = losses.item()
                    accumulate_loss.append(loss_value)

            logs['train_after_backpropagation'].append({'loss': sum(accumulate_loss, 0) / len(accumulate_loss)})

            #print(f'----> EPOCH SUMMARY TRAIN AFTER BACKPROP [{epoch}] -> [{i}/{len(data_loader_train)}]: ' + ', '.join([f'{k}: {v}' for k, v in logs['train_after_backpropagation'][epoch].items()]))

            # Test
            with torch.no_grad():
                accumulate_loss = []
                for i, data in tqdm(enumerate(data_loader_test), total=len(data_loader_test), desc=f'{house} - Epoch {epoch} - Test model with test data'):
                    images, targets, new_targets = data
                    images = list(image.to(device) for image in images)
                    new_targets = [{k: v.to(device).to(torch.int64) for k, v in t.items()} for t in new_targets]

                    with torch.cuda.amp.autocast(enabled=False):
                        loss_dict = model(images, new_targets)
                        losses = sum(loss for loss in loss_dict.values())

                    loss_value = losses.item()
                    accumulate_loss.append(loss_value)

            logs['test'].append({'loss': sum(accumulate_loss, 0) / len(accumulate_loss)})
            logs['validation'].append({'loss': 0})
            print(f'----> EPOCH {epoch} SUMMARY: ' + ', '.join([f'{k}: {v[epoch]}' for k, v in logs.items()]))

                    #plot_losses(logs)

            model.save(epoch=epoch,
                       optimizer_state_dict=optimizer.state_dict(),
                       lr_scheduler_state_dict=scheduler.state_dict(),
                       params=params,
                       logs=logs,
                       )
            # Change the model description on each epoch step
            if epoch == epochs_qualified_detectors[epoch_count] - 1 and epoch_count < len(epochs_qualified_detectors) -1:
                epoch_count += 1
                model.set_description(globals()[f'EXP_2_{house}_{gd_dataset}_EPOCHS_GD_{epochs_general}_EPOCHS_QD_{epochs_qualified_detectors[epoch_count]}_FINE_TUNE_{quantity}'.upper()])