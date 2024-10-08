import cv2
from PIL import Image
from torch.utils.data import DataLoader
from tqdm import tqdm

from doors_detection_long_term.doors_detector.dataset.torch_dataset import FINAL_DOORS_DATASET
from doors_detection_long_term.doors_detector.models.faster_rcnn import *
from doors_detection_long_term.doors_detector.models.model_names import FASTER_RCNN
from doors_detection_long_term.doors_detector.utilities.collate_fn_functions import collate_fn_faster_rcnn
from doors_detection_long_term.scripts.doors_detector.dataset_configurator import *
import torchvision.transforms as T

load_path = '/home/michele/Downloads/images'
save_path = '/home/michele/Downloads/test_faster_rcnn'

if not os.path.exists(save_path):
    os.mkdir(save_path)

images_names = os.listdir(load_path)
images_names.sort()
images_list = [cv2.imread(os.path.join(load_path, file_name)) for file_name in images_names]
model = FasterRCNN(model_name=FASTER_RCNN, n_labels=3, pretrained=True, dataset_name=FINAL_DOORS_DATASET, description=EXP_2_FLOOR4_GIBSON_EPOCHS_GD_60_EPOCHS_QD_40_FINE_TUNE_75)

model.eval()
model.to('cuda')
padding_height = 40
padding_width = 0
transform = T.Compose([
    T.ToTensor(),
    T.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    T.Pad([padding_width, padding_height]) # Check according image size
])
with torch.no_grad():
    for i, image in enumerate(images_list):

        img = transform(Image.fromarray(image[..., [2, 1, 0]])).unsqueeze(0).to('cuda')
        outputs = model.model(img)
        img_size = list(img.size()[2:])
        for im, output in zip([image], outputs):
            output = apply_nms(output)
            save_image = im.copy()
            for [x1, y1, x2, y2], label, conf in zip(output['boxes'], output['labels'], output['scores']):
                label, x1, y1, x2, y2 = label.item(), x1.item() - padding_width, y1.item() - padding_height, x2.item() - padding_width, y2.item() - padding_height
                label -= 1
                x1 = int(min(img_size[1], max(.0, x1)))
                y1 = int(min(img_size[0], max(.0, y1)))
                x2 = int(min(img_size[1], max(.0, x2)))
                y2 = int(min(img_size[0], max(.0, y2)))
                colors = {0: (0, 0, 255), 1: (0, 255, 0)}
                save_image = cv2.rectangle(save_image, (x1, y1), (x2, y2), colors[label])
            cv2.imwrite(os.path.join(save_path, 'image_{0:05d}.png'.format(i)), save_image)
