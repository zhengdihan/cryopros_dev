import os.path
import math
import argparse
import random
import numpy as np
import logging
from torch.utils.data import DataLoader
import torch
import site
import sys
import os

site_packages_dir = site.getsitepackages()[0]
package_path = os.path.join(site_packages_dir, "cryoPROS")
sys.path.append(package_path)

from data.dataset import Dataset
from models.model_hvae import HVAEModel

import sys

def parse_argument(json_path):

    parser = argparse.ArgumentParser(description='Training a conditional VAE deep neural network model from an input initial volume and raw particles with given imaging parameters.')

    parser.add_argument('--box_size'        , type=int  , help='box size'                , required = True)
    parser.add_argument('--Apix'            , type=float, help='pixel size in Angstrom'  , required = True)
    parser.add_argument('--init_volume_path', type=str  , help='input inital volume path', required = True)
    parser.add_argument('--data_path'       , type=str  , help='input raw particles path', required = True)
    parser.add_argument('--param_path'      , type=str  , help='path of star file which contains the imaging parameters', required = True)

    parser.add_argument('--gpu_ids', nargs='+', type=int, help='GPU IDs to utilize', required = True)
    
    parser.add_argument('--invert', action='store_true', help='invert the image sign')

    parser.add_argument('--opt'                   , type=str  , default=json_path , help='path to option JSON file')
    parser.add_argument('--task_name'             , type=str  , default='cryoPROS', help='task name')
    parser.add_argument('--volume_scale'          , type=float, default=50.0      , help='scale factor')
    parser.add_argument('--dataloader_batch_size' , type=int  , default=16        , help='batch size to load data')
    parser.add_argument('--dataloader_num_workers', type=int  , default=0         , help='number of workers to load data')
    parser.add_argument('--lr'                    , type=float, default=1e-4      , help='learning rate')
    parser.add_argument('--KL_weight'             , type=float, default=1e-4      , help='KL weight')
    parser.add_argument('--max_iter'              , type=int  , default=30000     , help='max number of iterations')

    if len(sys.argv) == 1:
        parser.print_help()
        exit()

    return parser.parse_args()

def main(json_path=os.path.join(package_path, 'options/train.json')):

    '''
    # ----------------------------------------
    # Step--1 (prepare opt)
    # ----------------------------------------
    '''
   
    args = parse_argument(json_path)

    from utils import utils_logger
    from utils import utils_image as util
    from utils import utils_option as option

    opt = option.parse(args, is_train=True)
    
    util.mkdirs((path for key, path in opt['path'].items() if 'pretrained' not in key))
    
    current_step = 0

    # --<--<--<--<--<--<--<--<--<--<--<--<--<-

    # ----------------------------------------
    # save opt to  a '../option.json' file
    # ----------------------------------------
    option.save(opt)

    # ----------------------------------------
    # return None for missing key
    # ----------------------------------------
    opt = option.dict_to_nonedict(opt)

    # ----------------------------------------
    # configure logger
    # ----------------------------------------
    logger_name = 'train'
    utils_logger.logger_info(logger_name, os.path.join(opt['path']['log'], logger_name+'.log'))
    logger = logging.getLogger(logger_name)
    logger.info(option.dict2str(opt))

    # ----------------------------------------
    # seed
    # ----------------------------------------
    seed = opt['train']['manual_seed']
    if seed is None:
        seed = random.randint(1, 10000)
    logger.info('Random seed: {}'.format(seed))
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)

    '''
    # ----------------------------------------
    # Step--2 (creat dataloader)
    # ----------------------------------------
    '''

    # ----------------------------------------
    # 1) create_dataset
    # 2) creat_dataloader for train and test
    # ----------------------------------------
    for phase, dataset_opt in opt['datasets'].items():
        if phase == 'train':
            train_set = Dataset(dataset_opt)
            train_size = int(math.ceil(len(train_set) / dataset_opt['dataloader_batch_size']))
            logger.info('Number of train images: {:,d}, iters: {:,d}'.format(len(train_set), train_size))        
            train_loader = DataLoader(train_set,
                                      batch_size=dataset_opt['dataloader_batch_size'],
                                      shuffle=dataset_opt['dataloader_shuffle'],
                                      num_workers=dataset_opt['dataloader_num_workers'],
                                      drop_last=True,
                                      pin_memory=True)
        elif phase == 'test':
            test_set = Dataset(dataset_opt)
            test_loader = DataLoader(test_set, batch_size=1,
                                     shuffle=False, num_workers=1,
                                     drop_last=False, pin_memory=True)
        else:
            raise NotImplementedError("Phase [%s] is not recognized." % phase)

    '''
    # ----------------------------------------
    # Step--3 (initialize model)
    # ----------------------------------------
    '''

    model = HVAEModel(opt)

    logger.info(model.info_network())
    model.init_train()
    logger.info(model.info_params())

    '''
    # ----------------------------------------
    # Step--4 (main training)
    # ----------------------------------------
    '''
    
    max_iter = opt['train']['max_iter'] if opt['train']['max_iter'] is not None else 30000
    break_flag = False

    for epoch in range(1000000):  # keep running
        for i, train_data in enumerate(train_loader):

            current_step += 1

            # -------------------------------
            # 1) feed patch pairs
            # -------------------------------
            model.feed_data(train_data)

            # -------------------------------
            # 2) optimize parameters
            # -------------------------------
            model.optimize_parameters(current_step)

            # -------------------------------
            # 3) update learning rate
            # -------------------------------
            model.update_learning_rate(current_step)

            # --------------------------
            # 4) training information
            # --------------------------
            if current_step % opt['train']['checkpoint_print'] == 0:
                logs = model.current_log()  # such as loss
                message = '<epoch:{:3d}, iter:{:8,d}, lr:{:.3e}> '.format(epoch, current_step, model.current_learning_rate())
                for k, v in logs.items():  # merge log information into message
                    message += '{:s}: {:.3e} '.format(k, v)
                logger.info(message)

            # -------------------------------
            # 5) save model
            # -------------------------------
            if current_step % opt['train']['checkpoint_save'] == 0:
                logger.info('Saving the model.')
                model.save(current_step)

            # -------------------------------
            # 6) testing
            # -------------------------------
            if current_step % opt['train']['checkpoint_test'] == 0:

                for i in range(opt['num_gen']):

                    img_name = '{:04d}'.format(i + 1)
                    img_dir = os.path.join(opt['path']['images'], img_name)
                    util.mkdir(img_dir)

                    model.test()

                    visuals = model.current_visuals()
                    img_G = util.tensor2uint_rescale(visuals['img_G'])
 
                    # -----------------------
                    # save 
                    # -----------------------
                    save_img_G_path = os.path.join(img_dir, '{:s}_{:d}_G.png'.format(img_name, current_step))
                    util.imsave(img_G, save_img_G_path)
                    
            if current_step > max_iter:
                break_flag = True
            
            if break_flag:
                break
       
        if break_flag:
            break
        
    logger.info('Saving the final model.')
    model.save('latest')
    logger.info('End of training.')


if __name__ == '__main__':
        
    main()
