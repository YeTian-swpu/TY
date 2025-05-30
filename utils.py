import torch
import torchvision.models as models
import torchvision.transforms as transforms

from PIL import Image
import numpy as np
import pandas as pd
import timm
import os
import torchvision

img_height, img_width = 224, 224
img_max, img_min = 1., 0

#cnn_model_paper = ['resnet18', 'resnet101', 'resnext50_32x4d', 'densenet121']
#vit_model_paper = ['vit_base_patch16_224', 'pit_b_224','visformer_small', 'swin_tiny_patch4_window7_224']

cnn_model_paper = ['resnet18', 'resnet101', 'resnext50_32x4d', 'densenet121', 'inception_v3', 'inception_resnet_v2', 'ens_adv_inception_resnet_v2']
vit_model_paper = ['inception_v4','vit_base_patch16_224', 'pit_b_224','visformer_small', 'swin_tiny_patch4_window7_224']


cnn_model_pkg = ['vgg19', 'resnet18', 'resnet101',
                 'resnext50_32x4d', 'densenet121', 'mobilenet_v2']
vit_model_pkg = ['vit_base_patch16_224', 'pit_b_224', 'cait_s24_224', 'visformer_small',
                 'tnt_s_patch16_224', 'levit_256', 'convit_base', 'swin_tiny_patch4_window7_224']

tgr_vit_model_list = ['vit_base_patch16_224', 'pit_b_224', 'cait_s24_224', 'visformer_small',
                      'deit_base_distilled_patch16_224', 'tnt_s_patch16_224', 'levit_256', 'convit_base']


def load_pretrained_model(cnn_model=[], vit_model=[]):
    for model_name in cnn_model:
        try:
            if model_name in dir(torchvision.models):
                # 使用 torchvision 加载标准模型
                yield model_name, getattr(torchvision.models, model_name)(pretrained=True)
            elif model_name == 'inception_resnet_v2':
                # 使用 timm 加载 inception_resnet_v2
                yield model_name, timm.create_model('inception_resnet_v2', pretrained=True)
            elif model_name == 'ens_adv_inception_resnet_v2':
                # 直接使用 timm 加载 ens_adv_inception_resnet_v2
                print(f"Loading {model_name} from cache.")
                yield model_name, timm.create_model('ens_adv_inception_resnet_v2', pretrained=True)
            else:
                print(f"Warning: {model_name} is not found in timm or torchvision.")
        except Exception as e:
            print(f"Error loading {model_name}: {e}")

    for model_name in vit_model:
        try:
            # 使用 timm 加载 ViT 模型
            yield model_name, timm.create_model(model_name, pretrained=True)
        except Exception as e:
            print(f"Error loading {model_name} from timm: {e}")
# def load_pretrained_model(cnn_model=[], vit_model=[]):
#     for model_name in cnn_model + vit_model:
#         try:
#             # 优先尝试加载 torchvision 模型
#             yield model_name, models.__dict__[model_name](weights="IMAGENET1K_V1")
#         except KeyError:
#             # 如果 torchvision 中未找到，则尝试加载 timm 模型
#             yield model_name, timm.create_model(model_name, pretrained=True)
    # for model_name in cnn_model:
    #     # yield model_name, models.__dict__[model_name](weights='DEFAULT')
    #     yield model_name, models.__dict__[model_name](weights="IMAGENET1K_V1")
    # for model_name in vit_model:
    #     yield model_name, timm.create_model(model_name, pretrained=True)


def wrap_model(model):
    """
    Add normalization layer with mean and std in training configuration
    """
    if hasattr(model, 'default_cfg'):
        """timm.models"""
        mean = model.default_cfg['mean']
        std = model.default_cfg['std']
    else:
        """torchvision.models"""
        mean = [0.485, 0.456, 0.406]
        std = [0.229, 0.224, 0.225]
    normalize = transforms.Normalize(mean, std)
    return torch.nn.Sequential(normalize, model)


def save_images(output_dir, adversaries, filenames):
    adversaries = (adversaries.detach().permute((0,2,3,1)).cpu().numpy() * 255).astype(np.uint8)
    for i, filename in enumerate(filenames):
        Image.fromarray(adversaries[i]).save(os.path.join(output_dir, filename))

def clamp(x, x_min, x_max):
    return torch.min(torch.max(x, x_min), x_max)


class EnsembleModel(torch.nn.Module):
    def __init__(self, models):
        super(EnsembleModel, self).__init__()
        # 将模型列表转换为 ModuleList 以便自动注册子模块
        self.models = torch.nn.ModuleList(models)
        self.softmax = torch.nn.Softmax(dim=1)
        self.type_name = 'ensemble'
        self.num_models = len(models)

    def forward(self, x, mode='mean'):
        outputs = []
        for model in self.models:
            outputs.append(model(x))
        outputs = torch.stack(outputs, dim=0)
        if mode == 'mean':
            outputs = torch.mean(outputs, dim=0)
            return outputs
        elif mode == 'ind':
            return outputs
        else:
            raise NotImplementedError



class AdvDataset(torch.utils.data.Dataset):
    def __init__(self, input_dir=None, output_dir=None, targeted=False, eval=False):
        self.targeted = targeted
        self.data_dir = input_dir
        self.f2l = self.load_labels(os.path.join(self.data_dir, 'labels.csv'))

        if eval:
            self.data_dir = output_dir
            # load images from output_dir, labels from input_dir/labels.csv
            print('=> Eval mode: evaluating on {}'.format(self.data_dir))
        else:
            self.data_dir = os.path.join(self.data_dir, 'images')
            print('=> Train mode: training on {}'.format(self.data_dir))
            print('Save images to {}'.format(output_dir))

    def __len__(self):
        return len(self.f2l.keys())

    def __getitem__(self, idx):
        filename = list(self.f2l.keys())[idx]

        assert isinstance(filename, str)

        filepath = os.path.join(self.data_dir, filename)
        image = Image.open(filepath)
        image = image.resize((img_height, img_width)).convert('RGB')
        # Images for inception classifier are normalized to be in [-1, 1] interval.
        image = np.array(image).astype(np.float32)/255
        image = torch.from_numpy(image).permute(2, 0, 1)
        label = self.f2l[filename]

        return image, label, filename

    def load_labels(self, file_name):
        dev = pd.read_csv(file_name)
        if self.targeted:
            f2l = {dev.iloc[i]['filename']: [dev.iloc[i]['label'],
                                             dev.iloc[i]['target_label']] for i in range(len(dev))}
        else:
            f2l = {dev.iloc[i]['filename']: dev.iloc[i]['label']
                   for i in range(len(dev))}
        return f2l


if __name__ == '__main__':
    dataset = AdvDataset(input_dir='./data_targeted',
                         targeted=True, eval=False)

    dataloader = torch.utils.data.DataLoader(
        dataset, batch_size=4, shuffle=False, num_workers=0)

    for i, (images, labels, filenames) in enumerate(dataloader):
        print(images.shape)
        # print(labels.shape)
        print(labels)
        print(filenames)
        break
