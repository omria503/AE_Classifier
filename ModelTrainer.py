from Config import *
from ClassifierModels import Resnet18
from AttentionUnetModel import AttentionUnet2D
from AEClassifierModels import BasicAutoEncoder, ImprovedAutoEncoder, \
    AE_Resnet18, IMPROVED_AE_Resnet18, AttentionUnetResnet18
from class_balanced_loss import CB_loss


class parameters():
    def __init__(self, lambda_loss=0.9, lr = 0.0001, weight_decay = 1e-5, decay_factor = 0.1, decay_patience = 5, batch_size=64, max_epoch=30):
        self.lr = lr
        self.weight_decay = weight_decay
        self.decay_factor = decay_factor
        self.decay_patience = decay_patience
        self.lambda_loss = lambda_loss
        self.batch_size = batch_size
        self.max_epoch = max_epoch


def data_augmentations(resize_target, crop_target, normalization_vec, rotation_angle=None, center_crop=False,
                       flip=True):
    transformList = []
    # optional augmentation:
    if resize_target is not None:
        transformList.append(transforms.Resize(resize_target))

    # basic augmentations:
    if center_crop:
        transformList.append(transforms.CenterCrop(crop_target))
    else:
        transformList.append(transforms.RandomCrop(crop_target))
    if flip:
        transformList.append(transforms.RandomHorizontalFlip())

    # optional augmentation
    if rotation_angle is not None:
        transformList.append(transforms.RandomRotation(rotation_angle, PIL.Image.BILINEAR))

    # basic augmentations (end)
    transformList.append(transforms.ToTensor())
    if normalization_vec is not None:
        transformList.append(transforms.Normalize(normalization_vec[0], normalization_vec[1]))

    transformSequence = transforms.Compose(transformList)
    return transformSequence


def plt_data(data_train, data_val, titleStr, save_fig=False, save_dir=''):
    fig1 = plt.figure()
    plt.xlabel('Epoch #')
    plt.plot(data_train, color='b', label='Train')
    plt.plot(data_val, color='r', label='Validation')
    titleStr_train = titleStr + '_Train'
    plt.title(titleStr_train)
    plt.legend(loc='upper right')
    if save_fig:
        fig1.savefig(save_dir + titleStr_train + '.png', dpi=100)
    plt.close('all')


def get_state_dict(modelCheckpoint):
    state_dict = modelCheckpoint['state_dict']
    new_state_dict = OrderedDict()
    for k, v in state_dict.items():
        if DATA_PARALLEL:
            if 'module.' not in k:
                k = 'module.' + k
        else:
            if 'module.' in k:
                k = k.replace('module.', '')
        new_state_dict[k] = v
    return new_state_dict


def get_lr(optimizer):
    for param_group in optimizer.param_groups:
        return param_group['lr']

class ModelTrainer:
    # ---- Train the densenet network
    # ---- pathDirData - path to the directory that contains images
    # ---- pathFileTrain - path to the file that contains image paths and label pairs (training set)
    # ---- pathFileVal - path to the file that contains image path and label pairs (validation set)
    # ---- nnArchitecture - model architecture 'DENSE-NET-121', 'DENSE-NET-169' or 'DENSE-NET-201'
    # ---- nnIsTrained - if True, uses pre-trained version of the network (pre-trained on imagenet)
    # ---- nnClassCount - number of output classes
    # ---- trBatchSize - batch size
    # ---- trMaxEpoch - number of epochs
    # ---- transResize - size of the image to scale down to (not used in current implementation)
    # ---- transCrop - size of the cropped image
    # ---- launchTimestamp - date/time, used to assign unique name for the checkpoint file
    # ---- checkpoint - if not None loads the model and continues training
    def __init__(self, device, architecture_type, num_of_input_channels, is_backbone_trained=True, num_classes=14,
                 balanced_classifier_loss=False, run_parameters=parameters()):
        self.architecture_type = architecture_type
        self.device = device
        self.num_classes = num_classes
        self.num_of_input_channels = num_of_input_channels
        self.lr = run_parameters.lr
        self.weight_decay = run_parameters.weight_decay
        self.decay_factor = run_parameters.decay_factor
        self.decay_patience = run_parameters.decay_patience
        self.run_parameters = run_parameters
        self.b_balanced_classifier_loss = balanced_classifier_loss
        print("Using balanced loss: " + str(balanced_classifier_loss))
        self.num_sample_per_label_train = []
        self.num_sample_per_label_val = []
        # -------------------- SETTINGS: NETWORK ARCHITECTURE
        if self.architecture_type not in COMBINED_ARCH:
            if self.architecture_type in CLASSIFIER_ARCH:
                if self.architecture_type == 'RES-NET-18':
                    self.model = Resnet18(self.num_classes, is_backbone_trained).to(self.device)
                else:
                    print(self.architecture_type, ' not supported in model trainer!')
                    exit()
                self.lambda_loss = 1  # Only classification
            elif self.architecture_type in AE_ARCH:
                if self.architecture_type == 'BASIC_AE':
                    self.model = BasicAutoEncoder().to(self.device)
                elif self.architecture_type == 'IMPROVED_AE':
                    self.model = ImprovedAutoEncoder().to(self.device)
                else:
                    print(self.architecture_type, ' not supported in model trainer!')
                    exit()
                self.lambda_loss = 0  # Only reconstruction
            else:
                print(self.architecture_type, ' not in any arch group! add to config file!')
                exit()
            if DATA_PARALLEL:
                self.model = torch.nn.DataParallel(self.model).to(self.device)
        else:
            if self.architecture_type == 'AE-RES-NET-18':
                self.model = AE_Resnet18(self.num_classes, is_backbone_trained).to(self.device)
            elif self.architecture_type == 'ATTENTION-AE-RES-NET-18':
                self.model = AttentionUnetResnet18(self.num_classes, is_backbone_trained).to(self.device)
            elif self.architecture_type == 'IMPROVED-AE-RES-NET-18':
                self.model = IMPROVED_AE_Resnet18(self.num_classes, is_backbone_trained).to(self.device)
            else:
                print(self.architecture_type, ' not supported in model trainer!')
                exit()
            self.lambda_loss = run_parameters.lambda_loss
            if DATA_PARALLEL:
                self.model.classifier = torch.nn.DataParallel(self.model.classifier).to(self.device)
                self.model.auto_encoder = torch.nn.DataParallel(self.model.auto_encoder).to(self.device)

        self.bce_logits_loss = torch.nn.BCEWithLogitsLoss(reduction='mean')
        self.mse_loss = torch.nn.MSELoss(reduction='mean')

    def load_checkpoint(self, checkpoint_classifier, checkpoint_encoder, checkpoint_combined, optimizer=None):
        modelCheckpoint = None
        if self.architecture_type in COMBINED_ARCH:
            if checkpoint_combined is not None:
                modelCheckpoint = torch.load(checkpoint_combined, map_location=self.device)
                self.model.load_state_dict(get_state_dict(modelCheckpoint))
                if optimizer is not None:
                    optimizer.load_state_dict(modelCheckpoint['optimizer'])
                loss_train_list = modelCheckpoint['loss_train_list']
                loss_validation_list = modelCheckpoint['loss_validation_list']
                init_epoch = modelCheckpoint['epoch']
            else:
                if checkpoint_classifier is not None:
                    modelCheckpoint = torch.load(checkpoint_classifier, map_location=self.device)
                    self.model.classifier.load_state_dict(get_state_dict(modelCheckpoint))
                if checkpoint_encoder is not None:
                    modelCheckpoint = torch.load(checkpoint_encoder, map_location=self.device)
                    self.model.auto_encoder.load_state_dict(get_state_dict(modelCheckpoint))
                loss_train_list = []
                loss_validation_list = []
                init_epoch = 0
        else:
            checkpoint = None
            if self.architecture_type in CLASSIFIER_ARCH:
                checkpoint = checkpoint_classifier
            elif self.architecture_type in AE_ARCH:
                checkpoint = checkpoint_encoder
            if checkpoint is not None:
                modelCheckpoint = torch.load(checkpoint, map_location=self.device)
                self.model.load_state_dict(get_state_dict(modelCheckpoint))
                if optimizer is not None:
                    optimizer.load_state_dict(modelCheckpoint['optimizer'])
                loss_train_list = modelCheckpoint['loss_train_list']
                loss_validation_list = modelCheckpoint['loss_validation_list']
                init_epoch = modelCheckpoint['epoch']
            else:
                loss_train_list = []
                loss_validation_list = []
                init_epoch = 0
        if modelCheckpoint is not None:
            if 'run_parameters' in modelCheckpoint.keys():
                run_parameters = modelCheckpoint['run_parameters']
            else:
                run_parameters = self.run_parameters
        else:
            run_parameters = self.run_parameters
        return loss_train_list, loss_validation_list, init_epoch, run_parameters

    def classifier_loss(self, varOutput, varTarget, is_train=False):
        if self.b_balanced_classifier_loss:
            if is_train:
                samples_per_cls = self.num_sample_per_label_train
            else:
                samples_per_cls = self.num_sample_per_label_val
            classifier_loss = CB_loss(labels=varTarget, logits=varOutput,
                                samples_per_cls=samples_per_cls, no_of_classes=self.num_classes,
                                loss_type="sigmoid", beta=0.9999, gamma=2, device=self.device)
        else:
            classifier_loss = self.bce_logits_loss(varOutput, varTarget)

        return classifier_loss

    def loss(self, varOutput, varTarget, varInput, is_train=False):
        if self.architecture_type in AE_ARCH:
            curr_loss = self.mse_loss(varOutput, varInput)
            display_loss = curr_loss.item()
        elif self.architecture_type in CLASSIFIER_ARCH:
            curr_loss = self.classifier_loss(varOutput, varTarget, is_train)
            display_loss = curr_loss.item()

        elif self.architecture_type in COMBINED_ARCH:
            curr_loss1 = self.mse_loss(varOutput[0], varInput)
            curr_loss2 = self.classifier_loss(varOutput[1], varTarget, is_train)
            display_loss = curr_loss2.item()
            curr_loss = self.lambda_loss * curr_loss2 + (1 - self.lambda_loss) * curr_loss1

        return curr_loss, display_loss

    def run_batch(self, input_img, target_label, train=False):
        varInput = torch.autograd.Variable(input_img).to(self.device)
        varTarget = torch.autograd.Variable(target_label).to(self.device)
        varOutput = self.model(varInput)
        loss_value, display_loss = self.loss(varOutput, varTarget, varInput,train)
        return loss_value, display_loss, varOutput

    def compute_AUROC(self, gt_data, prediction):
        """
        Computes area under ROC curve
        :param gt_data - ground truth data
        :param prediction - predicted data
        :return out_auroc - area under ROC curve vector (value for each class)
        """
        out_auroc = []

        np_gt_data = gt_data.cpu().numpy()
        np_prediction = prediction.cpu().numpy()

        for i in range(self.num_classes):
            if len(np.unique(np_gt_data[:, i])) != 2:
                out_auroc.append(-1)  # error, no data with that class!
            else:
                out_auroc.append(roc_auc_score(np_gt_data[:, i], np_prediction[:]))

        return out_auroc

    def epoch_train(self, epoch_id, data_loader, optimizer):
        self.model.train()
        loss_value_mean = 0
        for batch_id, (input_img, target_label) in enumerate(data_loader):
            # target_label = target_label.to(self.device, non_blocking=True)
            loss_value, display_loss, _ = self.run_batch(input_img, target_label, train=True)
            loss_value_mean += display_loss
            optimizer.zero_grad()
            loss_value.backward()
            optimizer.step()

            if batch_id % (int(len(data_loader) * 0.3)) == 0:
                print("----> EpochID: {}, BatchID/NumBatches: {}/{}, mean train loss: {}"
                      .format(epoch_id + 1, batch_id + 1, len(data_loader), loss_value_mean / (batch_id + 1)))

        loss_value_mean /= len(data_loader)
        return loss_value_mean

    def epoch_validation(self, data_loader):
        self.model.eval()
        loss_val = 0
        loss_val_norm = 0
        loss_tensor_mean = 0
        out_gt = torch.FloatTensor().to(self.device)
        out_pred = torch.FloatTensor().to(self.device)

        with torch.no_grad():
            for batch_id, (input_img, target_label) in enumerate(data_loader):
                if np.mod(batch_id, 10) == 0:
                    print('.', end="", flush=True)
                target_label = target_label.to(self.device, non_blocking=True)
                loss_value, display_loss, varOutput = self.run_batch(input_img, target_label)
                if self.architecture_type not in AE_ARCH:
                    if self.architecture_type in CLASSIFIER_ARCH:
                        predictions = torch.sigmoid(varOutput)
                    else:
                        predictions = torch.sigmoid(varOutput[1])
                    out_gt = torch.cat((out_gt, target_label), 0)
                    bs, c, h, w = input_img.size()
                    out_mean = predictions.view(bs, -1).mean(1)
                    out_pred = torch.cat((out_pred, out_mean.data), 0)

                loss_tensor_mean += loss_value
                loss_val += display_loss
                loss_val_norm += 1

        if self.architecture_type not in AE_ARCH:
            auroc_individual = self.compute_AUROC(out_gt, out_pred)
            auroc_mean = np.array(auroc_individual).mean()
        else:
            auroc_mean = 0
        out_loss = loss_val / loss_val_norm
        loss_tensor_mean = loss_tensor_mean / loss_val_norm
        return out_loss, loss_tensor_mean, auroc_mean

    def train(self, path_img_dir, path_file_train, path_file_validation, batch_size,
              max_epochs, trans_resize_size, trans_crop_size, trans_rotation_angle, launch_timestamp,
              checkpoint_classifier, checkpoint_encoder, checkpoint_combined):

        # -------------------- SETTINGS: DATA AUGMENTATION
        if self.architecture_type == 'RES-NET-18':
            normalization_vec = ([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
        else:
            normalization_vec = None
        transformSequence = data_augmentations(trans_resize_size, trans_crop_size,
                                               normalization_vec, trans_rotation_angle)

        transformSequence_val = data_augmentations(trans_resize_size, trans_crop_size,
                                                   normalization_vec, None, center_crop=True,
                                                   flip=False)
        # -------------------- SETTINGS: DATASET BUILDERS
        dataset_train = DatasetGenerator(pathImageDirectory=path_img_dir, pathDatasetFile=path_file_train,
                                         transform=transformSequence, num_img_chs=self.num_of_input_channels)
        dataset_validation = DatasetGenerator(pathImageDirectory=path_img_dir, pathDatasetFile=path_file_validation,
                                              transform=transformSequence_val, num_img_chs=self.num_of_input_channels)

        self.num_sample_per_label_train = dataset_train.num_sample_per_label
        self.num_sample_per_label_val = dataset_validation.num_sample_per_label

        dataLoader_train = DataLoader(dataset=dataset_train, batch_size=batch_size,
                                      shuffle=True, num_workers=8, pin_memory=True)
        dataLoader_validation = DataLoader(dataset=dataset_validation, batch_size=batch_size,
                                           shuffle=False, num_workers=8, pin_memory=True)

        # -------------------- SETTINGS: OPTIMIZER & SCHEDULER  # TODO: add parameters of the optimizer
        optimizer = optim.Adam(self.model.parameters(), lr=self.lr, betas=(0.9, 0.999), eps=1e-08, weight_decay=self.weight_decay)
        scheduler = ReduceLROnPlateau(optimizer, factor=self.decay_factor, patience=self.decay_patience, mode='min', verbose=True)

        # -------------------- LOAD CHECKPOINT
        loss_train_list, loss_validation_list, init_epoch,_ = self.load_checkpoint(checkpoint_classifier, checkpoint_encoder, checkpoint_combined, optimizer)

        # ---- TRAIN THE NETWORK
        min_loss = 100000
        max_auroc_mean = 0


        print('Run params: lr ', self.lr, ' weight decay ', self.weight_decay, ' patience ', self.decay_patience,
              ' lambda loss ', self.lambda_loss)
        s = time.time()
        loss_validation, loss_validation_tensor, auroc_mean = self.epoch_validation(dataLoader_validation)
        print('val epoch time: ', time.time() - s)
        torch.save({'model_type': self.architecture_type,
                    'epoch': 0,
                    'state_dict': self.model.state_dict(),
                    'best_loss': min_loss,
                    'optimizer': optimizer.state_dict(),
                    'loss_train_list': loss_train_list,
                    'loss_validation_list': loss_validation_list,
                    'run_parameters': self.run_parameters},
                   'm-' + self.architecture_type + '-' + launch_timestamp + '.pth.tar')
        print("-------> EpochID: {}/{}, mean validation loss: {}, AUROC mean: {}".format(init_epoch,
                                                                                         init_epoch + max_epochs,
                                                                                         loss_validation, auroc_mean))
        for epoch_id in range(0, max_epochs):
            timestampTime = time.strftime("%H%M%S")
            timestampDate = time.strftime("%d%m%Y")
            timestampSTART = timestampDate + '-' + timestampTime
            s = time.time()
            loss_train = self.epoch_train(epoch_id, dataLoader_train, optimizer)
            print('train epoch time: ', time.time() - s)
            s = time.time()
            loss_validation, loss_validation_tensor, auroc_mean = self.epoch_validation(dataLoader_validation)
            print('val epoch time: ', time.time() - s)
            print("-------> EpochID: {}/{}, mean train loss: {}".format(init_epoch + epoch_id + 1,
                                                                        init_epoch + max_epochs, loss_train))
            print("-------> EpochID: {}/{}, mean validation loss: {}, AUROC mean: {}".format(init_epoch + epoch_id + 1,
                                                                                             init_epoch + max_epochs,
                                                                                             loss_validation,auroc_mean))
            loss_train_list.append(loss_train)
            loss_validation_list.append(loss_validation)

            if epoch_id % 3 == 0:
                plt_data(loss_train_list, loss_validation_list, "Loss_train_vs_validation_" + self.architecture_type,
                         True, "")

            timestampTime = time.strftime("%H%M%S")
            timestampDate = time.strftime("%d%m%Y")
            timestampEND = timestampDate + '-' + timestampTime

            scheduler.step(loss_validation_tensor.item())

            if auroc_mean > max_auroc_mean:
                max_auroc_mean = auroc_mean
                min_loss = loss_validation
                min_loss_train = loss_train
                torch.save({'model_type': self.architecture_type,
                            'epoch': epoch_id + 1,
                            'state_dict': self.model.state_dict(),
                            'best_loss': min_loss,
                            'optimizer': optimizer.state_dict(),
                            'loss_train_list': loss_train_list,
                            'loss_validation_list': loss_validation_list},
                           'm-' + self.architecture_type + '-' + launch_timestamp + '.pth.tar')
                print('Epoch [' + str(epoch_id + 1) + '] [save] [' + timestampEND + '] loss= ' + str(
                    loss_validation) + ' lr=' + str(get_lr(optimizer)) + ' auroc mean=' + str(max_auroc_mean))
            else:
                print('Epoch [' + str(epoch_id + 1) + '] [----] [' + timestampEND + '] loss= ' + str(
                    loss_validation) + ' lr=' + str(get_lr(optimizer)) + ' auroc mean=' + str(max_auroc_mean))

            torch.save({'model_type': self.architecture_type,
                        'epoch': epoch_id + 1,
                        'state_dict': self.model.state_dict(),
                        'best_loss': min_loss,
                        'optimizer': optimizer.state_dict(),
                        'loss_train_list': loss_train_list,
                        'loss_validation_list': loss_validation_list},
                       'm-' + self.architecture_type + '-' + launch_timestamp + '_last.pth.tar')

        print("finish training!")
        return min_loss_train,min_loss

    # ---- Test the trained network
    # ---- pathDirData - path to the directory that contains images
    # ---- pathFileTrain - path to the file that contains image paths and label pairs (training set)
    # ---- pathFileVal - path to the file that contains image path and label pairs (validation set)
    # ---- nnArchitecture - model architecture 'DENSE-NET-121', 'DENSE-NET-169' or 'DENSE-NET-201'
    # ---- nnIsTrained - if True, uses pre-trained version of the network (pre-trained on imagenet)
    # ---- nnClassCount - number of output classes
    # ---- trBatchSize - batch size
    # ---- trMaxEpoch - number of epochs
    # ---- transResize - size of the image to scale down to (not used in current implementation)
    # ---- transCrop - size of the cropped image
    # ---- launchTimestamp - date/time, used to assign unique name for the checkpoint file
    # ---- checkpoint - if not None loads the model and continues training

    def test(self, path_img_dir, path_file_test, path_trained_model,batch_size, trans_resize_size, trans_crop_size):

        # CLASS_NAMES = ['Atelectasis', 'Cardiomegaly', 'Effusion', 'Infiltration', 'Mass', 'Nodule', 'Pneumonia',
        #                'Pneumothorax', 'Consolidation', 'Edema', 'Emphysema', 'Fibrosis', 'Pleural_Thickening',
        #                'Hernia']

        cudnn.benchmark = True  # TODO check what is that?

        checkpoint_classifier = None
        checkpoint_encoder = None
        checkpoint_combined = None
        if self.architecture_type in COMBINED_ARCH:
            checkpoint_combined = path_trained_model
        elif self.architecture_type in AE_ARCH:
            checkpoint_encoder = path_trained_model
        elif self.architecture_type in CLASSIFIER_ARCH:
            checkpoint_classifier = path_trained_model

        # -------------------- LOAD CHECKPOINT
        loss_train_list, loss_validation_list, init_epoch, run_parameters = self.load_checkpoint(checkpoint_classifier,
                                                                                 checkpoint_encoder,
                                                                                 checkpoint_combined)
        # -------------------- SETTINGS: DATA AUGMENTATION
        if self.architecture_type == 'RES-NET-18':
            normalization_vec = ([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
        else:
            normalization_vec = None

        transformSequence = data_augmentations(trans_resize_size, trans_crop_size,
                                               normalization_vec, None, center_crop=True, flip=False)

        dataset_test = DatasetGenerator(pathImageDirectory=path_img_dir, pathDatasetFile=path_file_test,
                                        transform=transformSequence, num_img_chs=self.num_of_input_channels)
        data_loader_test = DataLoader(dataset=dataset_test, batch_size=batch_size, num_workers=8,
                                      shuffle=False, pin_memory=True)

        self.num_sample_per_label_val = dataset_test.num_sample_per_label
        loss_test, _, auroc_mean = self.epoch_validation(data_loader_test)


        ind_name_start = len(path_trained_model) - path_trained_model[::-1].find('\\')
        ind_name_end = path_trained_model.find('.pth')

        name = path_trained_model[ind_name_start:ind_name_end]
        if self.architecture_type in COMBINED_ARCH:
            plt_data(loss_train_list, loss_validation_list,
                     name + '_decay_' + str(run_parameters.weight_decay) + '_lr_' + str(run_parameters.lr) + '_lambda_loss_' + str(run_parameters.lambda_loss) + '_AUROCmean_' + str(
                         np.round(1000 * auroc_mean) / 1000),True, "")
            print(path_trained_model, ' decay: ', run_parameters.weight_decay, ' lr: ', run_parameters.lr, ' lambda loss: ', run_parameters.lambda_loss, 'AUROC mean ', auroc_mean)
        elif self.architecture_type in AE_ARCH:
            plt_data(loss_train_list, loss_validation_list,
                     name + '_decay_' + str(run_parameters.weight_decay) + '_lr_' + str(run_parameters.lr) + '_lambda_loss_' + str(run_parameters.lambda_loss) ,True, "")
            print(path_trained_model, ' decay: ', run_parameters.weight_decay, ' lr: ', run_parameters.lr, ' lambda loss: ', run_parameters.lambda_loss)
        elif self.architecture_type in CLASSIFIER_ARCH:
            plt_data(loss_train_list, loss_validation_list,
                     name + '_decay_' + str(run_parameters.weight_decay) + '_lr_' + str(run_parameters.lr) + '_AUROCmean_' + str(
                         np.round(1000 * auroc_mean) / 1000),True, "")
            print(path_trained_model, ' decay: ', run_parameters.weight_decay, ' lr: ', run_parameters.lr, 'AUROC mean ', auroc_mean)

        return auroc_mean, loss_test
