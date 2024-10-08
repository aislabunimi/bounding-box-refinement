import random
import re
import time

from torch.optim import lr_scheduler
from tqdm import tqdm

from doors_detection_long_term.doors_detector.dataset.torch_dataset import DEEP_DOORS_2_LABELLED, FINAL_DOORS_DATASET
import numpy as np
import torch.optim
from torch.utils.data import DataLoader
from doors_detection_long_term.doors_detector.dataset.dataset_doors_final.datasets_creator_doors_final import DatasetsCreatorDoorsFinal
from doors_detection_long_term.doors_detector.models.model_names import YOLOv5
from doors_detection_long_term.doors_detector.models.yolov5 import *
from doors_detection_long_term.doors_detector.models.yolov5_repo.utils.general import check_amp
from doors_detection_long_term.doors_detector.models.yolov5_repo.utils.loss import ComputeLoss
from doors_detection_long_term.doors_detector.models.yolov5_repo.utils.torch_utils import smart_optimizer
from doors_detection_long_term.doors_detector.utilities.plot import plot_losses
from doors_detection_long_term.doors_detector.utilities.collate_fn_functions import collate_fn_yolov5
from doors_detection_long_term.scripts.doors_detector.dataset_configurator import *
from doors_detection_long_term.doors_detector.utilities.collate_fn_functions import seed_everything


device = 'cuda'

houses = ['house_1', 'house_2', 'house_7', 'house_9', 'house_10', 'house_13', 'house_15', 'house_20', 'house_21', 'house_22']
epochs_general_detector = [40, 60]
epochs_qualified_detectors = [20, 40]
fine_tune_quantity = [15, 25, 50, 75]

frozen_layers = 5

# Params
params = {
    #'epochs': 40,
    'batch_size': 4,
    'seed': 0
}
def prepare_model(description, reload_model, restart_checkpoint, epochs):
    model = YOLOv5Model(model_name=YOLOv5, n_labels=len(labels.keys()), pretrained=reload_model, dataset_name=FINAL_DOORS_DATASET, description=description)
    for n, p in model.named_parameters():
        p.requires_grad = True
        if int(re.findall(r'\d+', n)[0]) <= frozen_layers:
            p.requires_grad = False
            print(n)
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

    # Model parameters
    nl = de_parallel(model.model).model[-1].nl

    model.train()
    model.to('cuda')
    compute_loss = ComputeLoss(model.model)

    # General parameters
    nb = len(data_loader_train)
    nw = max(round(model.hyp['warmup_epochs'] * nb), 100)  # number of warmup iterations, max(3 epochs, 100 iterations)
    nbs = 64
    accumulate = max(round(nbs / params['batch_size']), 1)
    model.hyp['weight_decay'] *= params['batch_size'] * accumulate / nbs
    model.model.hyp['weight_decay'] *= params['batch_size'] * accumulate / nbs

    # Scaler
    amp = check_amp(model.model)
    scaler = torch.cuda.amp.GradScaler(enabled=amp)
    optimizer = smart_optimizer(model.model, 'SGD', model.hyp['lr0'], model.hyp['momentum'], model.hyp['weight_decay'])

    if restart_checkpoint:
        optimizer.load_state_dict(optimizer_state_dict)

    optimizer.zero_grad()

    lf = lambda x: (1 - x / epochs) * (1.0 - model.hyp['lrf']) + model.hyp['lrf']  # linear
    scheduler = lr_scheduler.LambdaLR(optimizer, lr_lambda=lf)

    if restart_checkpoint:
        scheduler.load_state_dict(lr_scheduler_state_dict)

    return model, compute_loss, optimizer, scheduler, scaler, start_epoch, nl, nw, nb, amp, nbs, accumulate, lf, logs


if __name__ == '__main__':

    # Fix seeds
    seed_everything(params['seed'])

    # Train the general detector with multiple epochs
    for house in houses:
        epoch_count = 0
        train, validation, test, labels, _ = get_final_doors_dataset_epoch_analysis(experiment=1, folder_name=house.replace('_', ''), train_size=0.25, use_negatives=False)
        print(f'Train set size: {len(train)}', f'Validation set size: {len(validation)}', f'Test set size: {len(test)}')
        data_loader_train = DataLoader(train, batch_size=params['batch_size'], collate_fn=collate_fn_yolov5, shuffle=False, num_workers=4)
        data_loader_validation = DataLoader(validation, batch_size=params['batch_size'], collate_fn=collate_fn_yolov5, drop_last=False, num_workers=4)
        data_loader_test = DataLoader(test, batch_size=params['batch_size'], collate_fn=collate_fn_yolov5, drop_last=False, num_workers=4)

        model, compute_loss, optimizer, scheduler, scaler, start_epoch, nl, nw, nb, amp, nbs, accumulate, lf, logs = prepare_model(globals()[f'EXP_1_{house}_{epochs_general_detector[0]}_EPOCHS'.upper()], reload_model=False, restart_checkpoint=False, epochs=epochs_general_detector[-1])
        print_logs_every = 10
        last_opt_step = -1
        model.to('cuda')

        start_time = time.time()

        for epoch in range(epochs_general_detector[-1]):

            temp_logs = {'train': [], 'train_after_backpropagation': [], 'validation': [], 'test': []}
            accumulate_loss = []

            model.train()
            optimizer.zero_grad()
            for d, data in tqdm(enumerate(data_loader_train), total=len(data_loader_train), desc=f'{house} - Epoch {epoch} - Train model'):
                ni = d + nb * epoch

                # warmup
                if ni <= nw:
                    xi = [0, nw]  # x interp
                    accumulate = max(1, np.interp(ni, xi, [1, nbs / params['batch_size']]).round())
                    for j, x in enumerate(optimizer.param_groups):
                        # bias lr falls from 0.1 to lr0, all other lrs rise from 0.0 to lr0
                        x['lr'] = np.interp(ni, xi, [model.hyp['warmup_bias_lr'] if j == 0 else 0.0, x['initial_lr'] * lf(epoch)])
                        if 'momentum' in x:
                            x['momentum'] = np.interp(ni, xi, [model.hyp['warmup_momentum'], model.hyp['momentum']])

                images, targets, converted_boxes = data
                images = images.to('cuda')

                with torch.cuda.amp.autocast(amp):
                    output = model(images)  # forward
                    loss, loss_items = compute_loss(output, converted_boxes.to('cuda'))
                    #print(loss_items)

                scaler.scale(loss).backward()

                if ni - last_opt_step >= accumulate:
                    scaler.unscale_(optimizer)  # unscale gradients
                    torch.nn.utils.clip_grad_norm_(model.model.parameters(), max_norm=10.0)  # clip gradients
                    scaler.step(optimizer)  # optimizer.step
                    scaler.update()
                    optimizer.zero_grad()
                    last_opt_step = ni

                accumulate_loss.append(loss.item())

            scheduler.step()
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
                for i, data in tqdm(enumerate(data_loader_train), total=len(data_loader_train), desc=f'{house} - Epoch {epoch} - Test model with training data'):
                    images, targets, converted_boxes = data

                    images = images.to('cuda')
                    output = model(images)
                    loss, loss_items = compute_loss(output, converted_boxes.to('cuda'))
                    accumulate_loss.append(loss.item())

            logs['train_after_backpropagation'].append({'loss': sum(accumulate_loss, 0) / len(accumulate_loss)})

            #print(f'----> EPOCH SUMMARY TRAIN AFTER BACKPROP [{epoch}] -> [{i}/{len(data_loader_train)}]: ' + ', '.join([f'{k}: {v}' for k, v in logs['train_after_backpropagation'][epoch].items()]))

            # Validation
            with torch.no_grad():
                accumulate_loss = []
                for i, data in tqdm(enumerate(data_loader_validation), total=len(data_loader_validation), desc=f'{house} - Epoch {epoch} - Test model with validation data'):
                    images, targets, converted_boxes = data

                    images = images.to('cuda')
                    output = model(images)
                    loss, loss_items = compute_loss(output, converted_boxes.to('cuda'))
                    accumulate_loss.append(loss.item())

            logs['validation'].append({'loss': sum(accumulate_loss, 0) / len(accumulate_loss)})

            # Test
            with torch.no_grad():
                accumulate_loss = []
                for i, data in tqdm(enumerate(data_loader_test), total=len(data_loader_test), desc=f'{house} - Epoch {epoch} - Test model with test data'):
                    images, targets, converted_boxes = data

                    images = images.to('cuda')
                    output = model(images)
                    loss, loss_items = compute_loss(output, converted_boxes.to('cuda'))
                    accumulate_loss.append(loss.item())

            logs['test'].append({'loss': sum(accumulate_loss, 0) / len(accumulate_loss)})
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
                model.set_description(globals()[f'EXP_1_{house}_{epochs_general_detector[epoch_count]}_EPOCHS'.upper()])


    # Qualify the general detectors trained before
    for house, epochs_general, quantity in [(h, e, q) for h in houses for e in epochs_general_detector for q in fine_tune_quantity]:
        epoch_count = 0
        print(f'{house}, general detectors trained for {epochs_general} epochs, fine tune train set: {quantity}')
        train, validation, test, labels, _ = get_final_doors_dataset_epoch_analysis(experiment=2, folder_name=house.replace('_', ''), train_size=quantity / 100, use_negatives=False)
        print(f'Train set size: {len(train)}', f'Validation set size: {len(validation)}', f'Test set size: {len(test)}')
        data_loader_train = DataLoader(train, batch_size=params['batch_size'], collate_fn=collate_fn_yolov5, shuffle=False, num_workers=4)
        data_loader_validation = DataLoader(validation, batch_size=params['batch_size'], collate_fn=collate_fn_yolov5, drop_last=False, num_workers=4)
        data_loader_test = DataLoader(test, batch_size=params['batch_size'], collate_fn=collate_fn_yolov5, drop_last=False, num_workers=4)

        model, compute_loss, optimizer, scheduler, scaler, start_epoch, nl, nw, nb, amp, nbs, accumulate, lf, logs = prepare_model(globals()[f'EXP_1_{house}_{epochs_general_detector[0]}_EPOCHS'.upper()], reload_model=True, restart_checkpoint=False, epochs=epochs_general_detector[-1])
        print_logs_every = 10
        last_opt_step = -1
        model.to('cuda')
        model.set_description(globals()[f'EXP_2_{house}_EPOCHS_GD_{epochs_general}_EPOCH_QD_{epochs_qualified_detectors[0]}_fine_tune_{quantity}'.upper()])
        print_logs_every = 10

        start_time = time.time()

        for epoch in range(epochs_qualified_detectors[-1]):

            temp_logs = {'train': [], 'train_after_backpropagation': [], 'validation': [], 'test': []}
            accumulate_loss = []

            model.train()
            optimizer.zero_grad()
            for d, data in tqdm(enumerate(data_loader_train), total=len(data_loader_train), desc=f'{house} - Epoch {epoch} - Fine tune with {quantity}% of examples'):
                ni = d + nb * epoch

                # warmup
                if ni <= nw:
                    xi = [0, nw]  # x interp
                    accumulate = max(1, np.interp(ni, xi, [1, nbs / params['batch_size']]).round())
                    for j, x in enumerate(optimizer.param_groups):
                        # bias lr falls from 0.1 to lr0, all other lrs rise from 0.0 to lr0
                        x['lr'] = np.interp(ni, xi, [model.hyp['warmup_bias_lr'] if j == 0 else 0.0, x['initial_lr'] * lf(epoch)])
                        if 'momentum' in x:
                            x['momentum'] = np.interp(ni, xi, [model.hyp['warmup_momentum'], model.hyp['momentum']])

                images, targets, converted_boxes = data
                images = images.to('cuda')

                with torch.cuda.amp.autocast(amp):
                    output = model(images)  # forward
                    loss, loss_items = compute_loss(output, converted_boxes.to('cuda'))

                scaler.scale(loss).backward()

                if ni - last_opt_step >= accumulate:
                    scaler.unscale_(optimizer)  # unscale gradients
                    torch.nn.utils.clip_grad_norm_(model.model.parameters(), max_norm=10.0)  # clip gradients
                    scaler.step(optimizer)  # optimizer.step
                    scaler.update()
                    optimizer.zero_grad()
                    last_opt_step = ni

                accumulate_loss.append(loss.item())

            scheduler.step()
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
                    images, targets, converted_boxes = data

                    images = images.to('cuda')
                    output = model(images)
                    loss, loss_items = compute_loss(output, converted_boxes.to('cuda'))
                    accumulate_loss.append(loss.item())

            logs['train_after_backpropagation'].append({'loss': sum(accumulate_loss, 0) / len(accumulate_loss)})

            #print(f'----> EPOCH SUMMARY TRAIN AFTER BACKPROP [{epoch}] -> [{i}/{len(data_loader_train)}]: ' + ', '.join([f'{k}: {v}' for k, v in logs['train_after_backpropagation'][epoch].items()]))

            # Validation
            with torch.no_grad():
                accumulate_loss = []
                for i, data in tqdm(enumerate(data_loader_validation), total=len(data_loader_validation), desc=f'{house} - Epoch {epoch} - Test model with validation data'):
                    images, targets, converted_boxes = data

                    images = images.to('cuda')
                    output = model(images)
                    loss, loss_items = compute_loss(output, converted_boxes.to('cuda'))
                    accumulate_loss.append(loss.item())

            logs['validation'].append({'loss': sum(accumulate_loss, 0) / len(accumulate_loss)})

            # Test
            with torch.no_grad():
                accumulate_loss = []
                for i, data in tqdm(enumerate(data_loader_test), total=len(data_loader_test), desc=f'{house} - Epoch {epoch} - Test model with test data'):
                    images, targets, converted_boxes = data

                    images = images.to('cuda')
                    output = model(images)
                    loss, loss_items = compute_loss(output, converted_boxes.to('cuda'))
                    accumulate_loss.append(loss.item())

            logs['test'].append({'loss': sum(accumulate_loss, 0) / len(accumulate_loss)})
            print(f'----> EPOCH {epoch} SUMMARY: ' + ', '.join([f'{k}: {v[epoch]}' for k, v in logs.items()]))

            plot_losses(logs)

            model.save(epoch=epoch,
                       optimizer_state_dict=optimizer.state_dict(),
                       lr_scheduler_state_dict=scheduler.state_dict(),
                       params=params,
                       logs=logs,
                       )
            # Change the model description on each epoch step
            if epoch == epochs_qualified_detectors[epoch_count] - 1 and epoch_count < len(epochs_qualified_detectors) -1:
                epoch_count += 1
                model.set_description(globals()[f'EXP_2_{house}_EPOCHS_GD_{epochs_general}_EPOCH_QD_{epochs_qualified_detectors[epoch_count]}_fine_tune_{quantity}'.upper()])