import torch.nn as nn
import torchvision

# classifiers based on ResNet
# all classifiers outputs are the logits, therefore should use Sigmoid to get the prediction probability!

class Resnet18(nn.Module):

    def __init__(self, num_classes, is_trained):
        super(Resnet18, self).__init__()

        self.resnet18 = torchvision.models.resnet18(pretrained=is_trained)

        kernelCount = self.resnet18.fc.in_features
        self.resnet18.fc = nn.Sequential(nn.Linear(kernelCount, num_classes))

    def forward(self, x):
        logits = self.resnet18(x)
        return logits


class Resnet34(nn.Module):

    def __init__(self, num_classes, is_trained):
        super(Resnet34, self).__init__()

        self.resnet34 = torchvision.models.resnet34(pretrained=is_trained)

        kernelCount = self.resnet34.fc.in_features
        self.resnet34.fc = nn.Sequential(nn.Linear(kernelCount, num_classes))

    def forward(self, x):
        x_logits = self.resnet34(x)
        return x_logits


class Resnet54(nn.Module):

    def __init__(self, num_classes, is_trained):
        super(Resnet54, self).__init__()

        self.resnet54 = torchvision.models.resnet54(pretrained=is_trained)

        kernelCount = self.resnet54.fc.in_features
        self.resnet54.fc = nn.Sequential(nn.Linear(kernelCount, num_classes))

    def forward(self, x):
        x_logits = self.resnet54(x)
        return x_logits


class DenseNet121(nn.Module):

    def __init__(self, num_classes, is_trained):
        super(DenseNet121, self).__init__()

        self.densenet121 = torchvision.models.densenet121(pretrained=is_trained)
        kernelCount = self.densenet121.classifier.in_features
        self.densenet121.classifier = nn.Sequential(nn.Linear(kernelCount, num_classes))

    def forward(self, x):
        x_logits = self.densenet121(x)
        return x_logits


class DenseNet169(nn.Module):
    
    def __init__(self, num_classes, is_trained):
        super(DenseNet169, self).__init__()

        self.densenet169 = torchvision.models.densenet169(pretrained=is_trained)
        kernelCount = self.densenet169.classifier.in_features
        self.densenet169.classifier = nn.Sequential(nn.Linear(kernelCount, num_classes))
        
    def forward (self, x):
        x_logits = self.densenet169(x)
        return x_logits


class DenseNet201(nn.Module):
    
    def __init__ (self, num_classes, is_trained):
        super(DenseNet201, self).__init__()
        
        self.densenet201 = torchvision.models.densenet201(pretrained=is_trained)
        kernelCount = self.densenet201.classifier.in_features
        self.densenet201.classifier = nn.Sequential(nn.Linear(kernelCount, num_classes))
        
    def forward (self, x):
        x_logits = self.densenet201(x)
        return x_logits
